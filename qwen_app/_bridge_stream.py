"""ChatBridge 功能切片 —— StreamMixin（流式）+ LegacyMockMixin（旧 mock 路径）。

拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
from datetime import datetime
from types import SimpleNamespace

from PyQt5.QtCore import pyqtSlot, QTimer

from .worker import WorkerThread
from . import config as _config
from . import chat_render


class StreamMixin(object):
    """真实流式路径：WorkerThread 桥接 / 流 buffer / 工具调用气泡 / 渲染辅助。"""

    def _collect_history_for_context(self, conv_id):
        """Day 20.6.22: 上下文记忆——把当前会话历史转成 LLM 可消费的 messages。

        与 chat_window.on_send_message 第 595 行 `messages = list(self.conversation_history)`
        同语义，但 QtQuick 路径之前完全没调用，模型每轮只看到当前 user 一条 → 「上下文不发送」。

        行为：
        - enable_context 关 / 无 conv_id / 历史为空 → 返回 []
        - 截取最近 N 条（N=_context_history_limit，默认 50；与 compressor
          COMPRESS_THRESHOLD=20 同数量级，加 30 条缓冲）
        - 过滤 __is_summary__ 这类内部标记（避免 LLM 看到系统摘要标记）
        - 只保留 {role, content} 两字段（OpenAI 协议不需要 time / is_summary 等元数据）
        - 单条 content 已在 _append_history 里做过 MAX_CONTENT_LEN 截断，这里
          信任 _append_history 的截断；超出本方法不再二次截断（避免重复截断污染）
        - history 项含工具调用元数据（tool_calls / tool_call_id）时丢弃该条
          —— QtQuick 路径历史上没把工具调用元数据写到 history（chat_window
          也没），防御性保留即可。
        """
        if not conv_id:
            return []
        if not getattr(self, "_enable_context", True):
            return []
        try:
            convs, _ = _config.load_conversations()
        except Exception as e:
            print(f"[chat_bridge] _collect_history_for_context 加载失败: {e}")
            return []
        conv = next((c for c in convs if c.get("id") == conv_id), None)
        if conv is None:
            return []
        raw = conv.get("history") or []
        if not raw:
            return []
        limit = int(getattr(self, "_context_history_limit", 50) or 50)
        tail = raw[-limit:]
        out = []
        for h in tail:
            if not isinstance(h, dict):
                continue
            role = h.get("role")
            content = h.get("content")
            if role not in ("user", "assistant", "system", "tool"):
                continue
            if not isinstance(content, str):
                continue
            # 跳过带工具调用结构化字段的历史项（防御性，正常情况下 history 不含）
            if "tool_calls" in h or "tool_call_id" in h:
                continue
            # 跳过 compressor 写入的摘要标记项（__is_summary__）——
            # 该字段是内部标记，不应让 LLM 看到；同时摘要本身的 content 是
            # 「以下是早期对话摘要：...」元注释，与真正的对话消息混排会
            # 让模型产生困惑。保留摘要的语义是「压进最近 50 条以内」，到
            # 这里时 compressor 应该已经把摘要前置到 history 头部与最近消息
            # 同发即可，单独一条混在 tail 里没有意义。
            if h.get("__is_summary__"):
                continue
            out.append({"role": role, "content": content})
        return out

    def start_real_chat(self, user_text: str, use_fake: bool = False, user_content=None,
                        system_prompt: str = ""):
        """Day 9: 启动一个真 WorkerThread。client 优先从 config 读取：
        - 能读到 api_key/base_url → 用真 LLM则实际发 HTTP 请求。会耗 token。
        - 读不到 / 错误 → 降级到 fake client（验证信号链路用）。

        读取逻辑：
        1. _config.get_current_model() → {api_key, base_url, model_id, proxy, ...}
        2. api_key 非空 → 生成真 client（会发出去）
        3. 否则 → fake

        Day 19 (C-NEW-1 / M-NEW-5 修复)：
        - WorkerThread 收到的 model_id 必须是当前激活模型的真实值，不再
          硬编码 "mock-qwen"
        - enable_thinking / enable_tools 从 self._enable_thinking / tools
          读（这些是用户在「偏好设置」里设的全局偏好；启动时从 cfg 顶层读）

        Day 19 (C-NEW-3 修复)：
        - system_prompt 是 send_message 构造的（专家 + 插件 skill + 工具引导），
          在这里注入到 messages 第一条。空字符串则不注入。
        """
        client = None
        model_name = "未选择"
        # 默认值：未配置模型时回退
        real_model_id = "mock-qwen"
        # Day 19: 思考/工具开关来自全局偏好（用户在「偏好设置」设的）
        # 不从 model dict 读（避免与「按模型设置」混淆）
        real_enable_thinking = getattr(self, "_enable_thinking", False)
        real_enable_tools = getattr(self, "_enable_tools", True)
        # Day 9+: use_fake=True 时跳过真 LLM（单元测试用，不烧 token）
        if use_fake:
            client = _make_fake_openai_client(user_text)
            self._last_model_name = "(fake)"
        else:
            try:
                current_model = _config.get_current_model()
                if current_model and current_model.get("api_key"):
                    model_name = current_model.get("name") or current_model.get("model_id", "?")
                    real_model_id = current_model.get("model_id", "mock-qwen")
                    client = _config.make_openai_client(
                        current_model.get("api_key", ""),
                        current_model.get("base_url", ""),
                        current_model.get("proxy", ""),
                    )
                    self._last_model_name = model_name
            except Exception as e:
                print(f"[chat_bridge] 读取 config 失败: {e}，降级到 fake client")

        if client is None:
            client = _make_fake_openai_client(user_text)
            self._last_model_name = "(fake)"

        # 注意：use_fake=True 分支已经在上面设过 _last_model_name，
        # 真 LLM 分支也在 try 块设过，这里只覆盖"真解析失败 fallback 到 fake"的分支

        # Day 19: 用真实 model_id / 来自全局偏好的 enable_thinking / enable_tools
        # max_rounds=3 是默认；自主模式可在偏好设置里调大（chat_window 路径会读）
        # Day 20.6.22: 上下文记忆（QtQuick 路径之前只发 system+user，备用窗口
        # chat_window.py 第 595 行 messages = list(self.conversation_history)）。
        # 现在按 enable_context 开关把当前会话 history 拼到 user 之前；关闭时
        # 回退单轮对话（仅 user 一条），与 chat_window 的 enable_context 同语义。
        user_msg = {"role": "user",
                    "content": (user_content if user_content is not None else user_text)}
        if system_prompt:
            base_messages = [{"role": "system", "content": system_prompt}]
        else:
            base_messages = []
        history_messages = self._collect_history_for_context(
            self._current_conv_id)
        # 顺序：system → 历史（最多 50 条）→ 当前 user
        messages = base_messages + history_messages + [user_msg]
        self._worker = WorkerThread(
            client=client,
            model_id=real_model_id,
            enable_thinking=real_enable_thinking,
            enable_tools=real_enable_tools,
            messages=messages,
            plugins=self._discover_plugins(),
            enabled_plugins=self._enabled_plugin_names(),
            max_rounds=3,                 # 最多 3 轮工具调用循环
            workspace=None,
        )
        # 桥接 worker 信号 → bridge 信号（再由 bridge 触发 QML）
        self._worker.chunk_received.connect(self._on_worker_chunk)
        self._worker.response_complete.connect(self._on_worker_complete)
        self._worker.error_occurred.connect(self._on_worker_error)
        # Day 11: 工具调用桥接
        self._worker.tool_call_start.connect(self._on_worker_tool_call_start)
        self._worker.tool_call_result.connect(self._on_worker_tool_call_result)
        # 线程 finished 也清 busy
        self._worker.finished.connect(self._on_worker_finished)
        # Day 18 (H2 修复)：WorkerThread 必须 deleteLater（每发一条消息会
        # new 一个新 QThread，不释放会泄漏）
        self._worker.finished.connect(lambda wt=self._worker: wt.deleteLater())
        # 启动
        self._set_busy(True)
        # Day 20.6.7 修复：不再预发「空 ai 占位气泡」。思考模型（QwQ 等）
        # 第一批流式 chunk 是 thinking —— _on_worker_chunk 的切段逻辑看到
        # who != _last_who 就另建 thinking 气泡，正文再建第三个，预占位的
        # 空 ai 气泡永远没人填 → 「每次对话后有一个空回复」。
        # 改为首个 chunk 到来时由切段逻辑建气泡（_last_who 初始为 None，
        # None != 任何 who 必然触发 messageAdded），无响应/中断时也不会留空气泡。
        # Day 18 (H1)：启动 worker 时 +1 计数（reload_plugins 据此判断能否重载）
        with self._worker_count_lock:
            self._worker_count += 1
        self._worker.start()

    def _on_worker_chunk(self, content: str, is_thinking: bool):
        """WorkerThread.chunk_received → QML.appendToLast

        Day 19 (H-NEW-5 修复): _stream_buffers[who] 累积超 MAX_STREAM_BUFFER
        时裁掉前段，保留最后 1MB（200k 中文字远超上下文需求）。防止
        恶意/失控 LLM 服务端无限流式响应把进程 OOM 死。
        """
        who = "thinking" if is_thinking else "ai"
        if self._last_who != who:
            # 切段（思考 → 回答）：新建气泡
            ts = datetime.now().strftime("%H:%M")
            self.messageAdded.emit(who, self._mk_msg("", ts))
            self._last_who = who
        # Day 6: 累积 buffer（流式期间用 PlainText 显示，finalize 时用 Markdown 替换）
        buf = self._stream_buffers.get(who, "") + content
        if len(buf) > self.MAX_STREAM_BUFFER:
            # 裁前段保留后 1MB
            buf = buf[-self.MAX_STREAM_BUFFER:]
        self._stream_buffers[who] = buf
        self.appendToLast.emit(who, content)

    def _on_worker_complete(self, full: str):
        """WorkerThread.response_complete → finalizeLast + 渲染 Markdown → messageReplaced"""
        if self._last_who:
            self.finalizeLast.emit(self._last_who)
            # Day 6: 流式结束后用 markdown_to_html 渲染整段 → 替换气泡为富文本
            self._flush_stream_buffer(self._last_who)
        self._last_who = None
        # Day 14 候选: 提前清 busy（QThread.finished 不可靠时兜底）
        self._set_busy(False)

    def _flush_stream_buffer(self, who: str):
        """Day 6: 取 buffer → markdown_to_html → emit messageReplaced 让 QML 替换
        Day 10: 同时把 assistant 消息写到 SQLite（response_complete / stop / error 三种收尾都走这里）
        """
        buf = self._stream_buffers.pop(who, "")
        if not buf:
            return
        # Day 10: assistant 消息保存到 SQLite（仅当 who == "ai"，避免错误气泡也保存）
        if who == "ai":
            self._append_history("assistant", buf)
        # A1: 通过 chat_render 抽象层（QtQuick 走 RichText）
        rendered = chat_render.render_text_final(who, buf, self._theme)
        self.messageReplaced.emit(who, rendered)

    def _on_worker_error(self, msg: str):
        """WorkerThread.error_occurred → QML.appendError"""
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        self.appendError.emit("error", f"{msg} ({ts})")
        # 错误时收尾（flush buffer + finalize + 解 busy）
        if self._last_who:
            self._flush_stream_buffer(self._last_who)
            self.finalizeLast.emit(self._last_who)
            self._last_who = None

    def _on_worker_finished(self):
        """QThread.finished 兜底：万一 signal 漏了，确保清 busy + 释放 QThread"""
        self._set_busy(False)
        # Day 15: 释放 WorkerThread（避免每发一条消息泄漏一个 QThread）
        if self._worker is not None:
            w = self._worker
            self._worker = None
            # deleteLater 在事件循环里 GC，比直接 delete 安全
            w.deleteLater()
        # Day 18 (H1)：worker 退出时计数 -1；若之前 reload 因计数 > 0 被推迟，
        # 现在重新尝试一次
        with self._worker_count_lock:
            self._worker_count = max(0, self._worker_count - 1)
            pending = self._worker_count == 0 and getattr(self, "_reload_pending", False)
            if pending:
                self._reload_pending = False
        if pending:
            QTimer.singleShot(0, self._do_reload_plugins)

    # ============ Day 11: 工具调用信号桥接 ============
    def _on_worker_tool_call_start(self, name: str, args_str: str):
        """WorkerThread.tool_call_start → QML 显示工具调用气泡

        实现方式：往 messageModel append 一个 who="tool_call" 的条目，
        QML delegate 看到这种类型会渲染成折叠卡片样式。
        同时 emit toolCallStarted 给 QML 端可能要做的特殊处理。
        """
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        # 解析 args（如果有的话）
        args_display = args_str
        try:
            import json as _json
            args_obj = _json.loads(args_str) if args_str else {}
            args_display = _json.dumps(args_obj, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # 推到 messageModel（who="tool_call"，text=name，code=args）
        self.messageAdded.emit("tool_call", self._mk_msg(name, ts, args_display))
        self.toolCallStarted.emit(name, args_str)

    def _on_worker_tool_call_result(self, name: str, args_str: str, result: str):
        """WorkerThread.tool_call_result → QML 显示工具结果气泡

        推到 messageModel（who="tool_result"，text=name，code=result_display）
        同时 emit toolCallResult(name, {args, result}) 给 QML 端可能要做的特殊处理。
        """
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        # 截断过长结果（避免气泡爆长）
        result_display = result if len(result) <= 800 else (result[:800] + "\n... (已截断)")
        self.messageAdded.emit("tool_result", self._mk_msg(name, ts, result_display))
        # Day 19.1 修订：toolCallResult 改为 (name, QVariantMap) ≤2 参数，
        # 防 QTBUG-94360（QML Connections 监听 ≥3 参数信号栈越界崩溃）。
        self.toolCallResult.emit(name, {"args": args_str, "result": result})

    def _mk_msg(self, text: str, ts: str, code: str = "") -> dict:
        """Day 17: messageAdded 的负载（收敛为单个 QVariantMap，规避 QTBUG-94360）

        Day 20.4.3: text 已**预先渲染**（Python 侧调 chat_render / html.escape），
        QML 端 textFormat: Text.RichText 直接显示。**不能**在这里再 escape 一
        次（会双重转义），也不能传原始文本（会撞 RichText 解析导致内容消失）。

        QML 端用 `code.length > 0` 推导 hasCode，因此不再单独传该字段。
        """
        return {"text": text or "", "ts": ts or "", "code": code or ""}

    @staticmethod
    def _render_for_qml(role: str, content: str, theme: str = "light") -> str:
        """Day 20.4.3: 把消息内容渲染成 QML RichText 可安全显示的字符串。

        三个角色三种处理：
        - user：原始输入 → html.escape（防 RichText 把 ``<stdio.h>`` 误读成标签）
        - assistant：原始 AI 输出 → markdown_to_html（保留代码块/链接/列表等格式）
        - tool_call / tool_result：JSON 摘要 → html.escape（避免参数里的 ``<`` / ``&`` 撞破 HTML）

        之前所有路径都直接把 raw text 塞给 QML.Text (RichText)：
        - AI history reload 时 markdown 不会渲染（用户看到的"内容消失"）
        - 用户文本含 ``<`` 时 RichText 解析失败 → Text 控件整段空白
        - tool_call 的 JSON 含 ``<tool>`` 之类被误读为标签

        修复：所有 messageAdded / messageReplaced / sessionLoaded emit 前
        统一过这道工序。
        """
        if not content:
            return ""
        if role in ("tool_call", "tool_result", "system"):
            import html
            return html.escape(content, quote=False)
        if role == "user":
            import html
            return html.escape(content, quote=False)
        # role == "assistant" 或其它：走 markdown 渲染
        return chat_render.render_text_final("ai", content, theme)


class LegacyMockMixin(object):
    """Day 1-2 兼容：旧 mock 路径（保留但默认不用）。"""

    @pyqtSlot(str)
    def send_message_mock(self, text: str):
        """Day 1-2 用的 mock 路径（无 WorkerThread，纯 QTimer 推流式）。"""
        if not text or not text.strip():
            return
        self._send_user_bubble(text.strip())
        QTimer.singleShot(500, lambda: self._simulate_ai_reply(text))

    # ============ Day 1-2 兼容：旧 mock 路径（保留但默认不用）============
    def _simulate_ai_reply(self, prompt: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        self._last_who = "ai"
        self.messageAdded.emit("ai", self._mk_msg("", ts))
        replies = [
            f"我理解你问的是：{prompt[:30]}...",
            "这是 mock 路径（Day 1-2 用），send_message 已默认改走真实路径。",
            "如果你看到这条，说明 mock 桥接工作正常 ✓"
        ]
        self.appendToLast.emit("ai", replies[0])
        QTimer.singleShot(600, lambda: self._push_chunk(replies[1]))
        QTimer.singleShot(1200, lambda: self._push_chunk(replies[2]))
        QTimer.singleShot(1500, self._finish_simulate)

    def _push_chunk(self, content: str):
        self.appendToLast.emit("ai", "\n\n" + content)

    def _finish_simulate(self):
        self.finalizeLast.emit("ai")
        self._last_who = None


# ============ 模块级：fake OpenAI client ============
def _make_fake_openai_client(user_text: str):
    """构造一个能跑通 WorkerThread.run() 的假 OpenAI client。

    模仿 openai.OpenAI 的 chat.completions.create(..., stream=True) 返回迭代器，
    每个 chunk 有 choices[0].delta.content / .reasoning_content。
    """
    def make_chunk(text: str = "", reasoning: str = "", is_last: bool = False):
        delta = SimpleNamespace(content=text or None,
                               reasoning_content=reasoning or None,
                               tool_calls=None)
        choice = SimpleNamespace(index=0, delta=delta, finish_reason="stop" if is_last else None)
        return SimpleNamespace(id="mock", choices=[choice], model="mock-qwen", object="chat.completion.chunk")

    # Day 6: 故意包含 markdown 语法（加粗 / 行内代码 / 围栏代码 / 列表 / 标题）
    # 流式阶段 PlainText 显示，finalize 时 messageReplaced 触发 markdown 渲染版
    text = (
        f"你说的是 **" + user_text + "**。\n\n"
        "下面给你看个 Python `quicksort` 的实现：\n\n"
        "```python\n"
        "def quicksort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    pivot = arr[0]\n"
        "    left = [x for x in arr[1:] if x < pivot]\n"
        "    right = [x for x in arr[1:] if x >= pivot]\n"
        "    return quicksort(left) + [pivot] + quicksort(right)\n"
        "```\n\n"
        "**关键点**：\n"
        "- 时间复杂度 O(n log n)\n"
        "- 空间复杂度 O(n)\n"
        "- 不稳定排序\n"
    )
    chunk_size = 15
    pieces = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    chunks = [make_chunk(p) for p in pieces] + [make_chunk(is_last=True)]

    class FakeCompletions:
        def create(self, **kwargs):
            def gen():
                # 不在 fake 里 sleep（会引起 offscreen QML segfault）。
                # 流式延迟在 QML 端用 QTimer 间隔 emit。
                for c in chunks:
                    yield c
            return gen()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    return FakeClient()
