"""WorkerThread — 流式 API 请求 + 思考标签解析 + 工具调用循环

线程安全改进：
- 使用 threading.Event 替代裸布尔标志，确保跨线程可见性
- 添加 API 超时保护（默认 60 秒）
- 添加资源清理（finally 块关闭流式连接）
- stop() 方法设置事件信号，run() 中每轮迭代检查
"""
import json
import threading
from PyQt5.QtCore import QThread, pyqtSignal
from .plugin_manager import get_enabled_tools, dispatch_tool


class WorkerThread(QThread):
    chunk_received = pyqtSignal(str, bool)       # (内容, 是否为思考)
    response_complete = pyqtSignal(str)           # 完整的非思考回复文本
    error_occurred = pyqtSignal(str)
    tool_call_start = pyqtSignal(str, str)        # (工具名, 参数JSON)
    tool_call_result = pyqtSignal(str, str, str)  # (工具名, 参数, 结果)

    START_TAGS = ["\u8fea\u58eb", "<think>", "<thinking>"]
    END_TAGS = ["iever", "</think>", "</thinking>"]
    API_TIMEOUT = 60  # API 请求超时秒数

    def __init__(self, client, model_id, enable_thinking, enable_tools, messages,
                 plugins=None, enabled_plugins=None, parent=None, max_rounds=5,
                 workspace=None):
        super().__init__(parent)
        self.client = client
        self.model_id = model_id
        self.enable_thinking = enable_thinking
        self.enable_tools = enable_tools
        self.messages = messages
        self.plugins = plugins or {}
        self.enabled_plugins = enabled_plugins or []
        self.max_rounds = max_rounds   # 工具调用循环最大轮次（自主模式可调大）
        self._stop_event = threading.Event()  # 线程安全停止信号
        self.workspace = workspace  # 当前对话的工作目录；run() 内 set_active + finally clear

    def _find_first_tag(self, text, tags):
        best_pos = -1
        best_tag = None
        for tag in tags:
            pos = text.find(tag)
            if pos != -1 and (best_pos == -1 or pos < best_pos):
                best_pos = pos
                best_tag = tag
        return best_pos, best_tag

    def run(self):
        completion = None
        try:
            # 设置当前对话的工作目录作为插件调用上下文；
            # finally 里 clear_active_workspace 避免线程间状态泄漏
            # （多 worker 并发、跨对话切换时不读到对方的 workspace）
            if self.workspace:
                try:
                    from .workspace import set_active_workspace
                    set_active_workspace(self.workspace)
                except Exception:
                    pass

            messages = list(self.messages)
            extra = {}
            if self.enable_thinking:
                extra["enable_thinking"] = True

            MAX_ROUNDS = self.max_rounds  # 最多工具调用轮次（自主模式可调大）

            for round_num in range(MAX_ROUNDS + 1):
                # 每轮开始前检查停止信号
                if self._stop_event.is_set():
                    return

                kwargs = {
                    "model": self.model_id,
                    "messages": messages,
                    "extra_body": extra,
                    "stream": True,
                    "timeout": self.API_TIMEOUT,
                }
                if self.enable_tools and self.enabled_plugins and round_num < MAX_ROUNDS:
                    plugin_tools = get_enabled_tools(self.plugins, self.enabled_plugins)
                    if plugin_tools:
                        kwargs["tools"] = plugin_tools
                        kwargs["tool_choice"] = "auto"

                completion = self.client.chat.completions.create(**kwargs)

                # 处理整段流式输出（思考标签 / 内容 / 工具调用累积）
                full_response, has_tool_calls, tool_calls_acc = self._handle_stream(completion)
                # 流中检测到停止信号：直接退出，不触发 response_complete
                if self._stop_event.is_set():
                    return

                # 没有工具调用 → 本轮即最终回复
                if not has_tool_calls:
                    self.response_complete.emit(full_response)
                    return

                # 把助手的工具调用意图写回消息历史，并执行工具
                tc_items = sorted(tool_calls_acc.items(), key=lambda x: x[0])
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [item[1] for item in tc_items]
                })
                self._execute_tools(tc_items, messages)

            # 超过最大轮次
            self.error_occurred.emit("工具调用轮次超过上限，已终止")

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                from .workspace import clear_active_workspace
                clear_active_workspace()
            except Exception:
                pass
            self._safe_close(completion)

    def _handle_stream(self, completion):
        """处理单个流式响应的全部 chunk。

        返回 (full_response, has_tool_calls, tool_calls_acc)：
        - full_response：剔除思考内容后的正式回复文本
        - has_tool_calls / tool_calls_acc：本轮回传的工具调用
        流处理中若置位停止信号，会安全关闭连接并提前返回（不抛异常）。
        """
        tool_calls_acc = {}
        has_tool_calls = False
        in_thinking = False
        full_response = ""
        content_buffer = ""

        for chunk in completion:
            if self._stop_event.is_set():
                self._safe_close(completion)
                return full_response, has_tool_calls, tool_calls_acc
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # 工具调用累积（跨 chunk 拼接 name / arguments）
            if self.enable_tools and hasattr(delta, "tool_calls") and delta.tool_calls:
                has_tool_calls = True
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""}
                        }
                    entry = tool_calls_acc[idx]
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["function"]["arguments"] += tc.function.arguments
                continue

            # 内容流处理（带缓冲防标签切割）
            if hasattr(delta, "content") and delta.content:
                content_buffer += delta.content
                while content_buffer:
                    if in_thinking:
                        pos, tag = self._find_first_tag(content_buffer, self.END_TAGS)
                        if tag:
                            before = content_buffer[:pos]
                            if before:
                                self.chunk_received.emit(before, True)
                            content_buffer = content_buffer[pos + len(tag):]
                            in_thinking = False
                        else:
                            safe_len = len(content_buffer)
                            for t in self.END_TAGS:
                                for i in range(1, min(len(t), len(content_buffer)) + 1):
                                    if content_buffer.endswith(t[:i]):
                                        safe_len = min(safe_len, len(content_buffer) - i)
                            if safe_len > 0:
                                self.chunk_received.emit(content_buffer[:safe_len], True)
                            content_buffer = content_buffer[safe_len:]
                            break
                    else:
                        pos, tag = self._find_first_tag(content_buffer, self.START_TAGS)
                        if tag:
                            before = content_buffer[:pos]
                            if before:
                                self.chunk_received.emit(before, False)
                                full_response += before
                            content_buffer = content_buffer[pos + len(tag):]
                            in_thinking = True
                        else:
                            safe_len = len(content_buffer)
                            for t in self.START_TAGS:
                                for i in range(1, min(len(t), len(content_buffer)) + 1):
                                    if content_buffer.endswith(t[:i]):
                                        safe_len = min(safe_len, len(content_buffer) - i)
                            if safe_len > 0:
                                self.chunk_received.emit(content_buffer[:safe_len], False)
                                full_response += content_buffer[:safe_len]
                            content_buffer = content_buffer[safe_len:]
                            break
                continue

            # 标准 reasoning_content 字段（DeepSeek-R1 等模型使用独立字段）
            # 注意：不能设置 in_thinking = True，否则后续 content 字段的
            # 正式回复会被误判为思考内容。reasoning_content 和 content
            # 是两个独立通道，in_thinking 仅用于 content 内的标签解析。
            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                self.chunk_received.emit(delta.reasoning_content, True)
                continue

        # flush 剩余缓冲
        if content_buffer:
            if in_thinking:
                self.chunk_received.emit(content_buffer, True)
            else:
                self.chunk_received.emit(content_buffer, False)
                full_response += content_buffer

        return full_response, has_tool_calls, tool_calls_acc

    def _execute_tools(self, tc_items, messages):
        """按序执行本轮回传的工具调用，并把结果追加进 messages（就地修改）。"""
        for idx, tc in tc_items:
            if self._stop_event.is_set():
                return
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]
            self.tool_call_start.emit(name, args_str)
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            result = dispatch_tool(self.plugins, self.enabled_plugins, name, args)
            self.tool_call_result.emit(name, args_str, result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result
            })

    @staticmethod
    def _safe_close(completion):
        """安全关闭流式连接，忽略关闭异常"""
        if completion is not None:
            try:
                if hasattr(completion, 'close'):
                    completion.close()
            except Exception:
                pass

    def stop(self):
        """线程安全停止 — 设置事件信号，run() 中检查并退出"""
        self._stop_event.set()

    def is_stopped(self):
        """查询是否已收到停止信号"""
        return self._stop_event.is_set()
