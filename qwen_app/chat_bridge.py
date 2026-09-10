"""ChatBridge — Python ↔ QML 双向通信桥。

阶段 2 Day 1-2：mock 路径（QML ↔ Python 信号骨架）
阶段 2 Day 3-5：real_worker 路径（用 fake OpenAI client 启动 WorkerThread，
                 把 chunk_received/response_complete/error_occurred
                 桥到 QML）

QML 端不需要知道走的是 mock 还是 real 路径，看到的都是：
- send_message(text) — 用户发送
- messageAdded(who, text, ts, hasCode, code) — 新气泡
- appendToLast(who, content) — 流式追加
- finalizeLast(who) — 完成
- appendError(who, text) — 错误气泡
- themeChanged(name) — 主题切换
"""
from datetime import datetime
from types import SimpleNamespace
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty, QTimer

from .worker import WorkerThread
from .theme import markdown_to_html
from . import config as _config


class ChatBridge(QObject):

    # ============ Python → QML 信号 ============
    messageAdded = pyqtSignal(str, str, str, bool, str)   # who, text, ts, hasCode, code
    appendToLast = pyqtSignal(str, str)                    # who, content
    finalizeLast = pyqtSignal(str)                          # who
    appendError = pyqtSignal(str, str)                      # who, text
    themeChanged = pyqtSignal(str)                          # "light" | "dark"
    messageReplaced = pyqtSignal(str, str)                  # who, rendered_html (Day 6)
    # Day 8: 会话管理
    sessionListChanged = pyqtSignal()                            # 会话列表变化（QML 重拉）
    sessionLoaded = pyqtSignal(str, 'QVariantList')              # conv_id, history list
    currentModelNameChanged = pyqtSignal(str)                   # Day 10: 切换模型名名发生变化
    # Day 11: 工具调用
    toolCallStarted = pyqtSignal(str, str)                       # (工具名, 参数JSON)
    toolCallResult = pyqtSignal(str, str, str)                   # (工具名, 参数, 结果)

    # ============ 内部状态（暴露给测试/QML 读）============
    busyChanged = pyqtSignal(bool)                          # Day 7: 发送/停止按钮切换的 NOTIFY 信号

    def _get_busy(self) -> bool:
        return self._is_busy

    def _set_busy(self, busy: bool):
        if busy != self._is_busy:
            self._is_busy = busy
            self.busyChanged.emit(busy)

    isBusy = pyqtProperty(bool, _get_busy, _set_busy, notify=busyChanged)

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self._theme = theme
        self._is_busy = False   # Day 7: pyqtProperty backend (use _set_busy to update)
        self._last_who = None      # 当前正在流式的气泡 who
        # Day 6: 累积 buffer（按 who 维度），finalize 时用 markdown_to_html 渲染
        self._stream_buffers = {}
        self._worker = None  # Day 7: 在初始化时就设为 None（避免 stop_chat 报 AttributeError）
        # Day 8: 会话状态（从 SQLite 加载当前 conv_id）
        try:
            _convs, _cur = _config.load_conversations()
            self._current_conv_id = _cur
        except Exception:
            self._current_conv_id = None
        self._last_model_name = "(none)"  # Day 9: 最后使用的模型名（错误提示用）
        # Day 9: 启动时立即从 config 读当前模型名（QML 顶栏要显示）
        try:
            _m = _config.get_current_model()
            if _m:
                self._last_model_name = _m.get("name") or _m.get("model_id", "?")
        except Exception:
            pass

    # ============ QML → Python Slot ============
    @pyqtSlot(str)
    def send_message(self, text: str, use_fake: bool = False):
        """QML 触发用户发送一条消息。

        Day 3-5 起默认走 real 路径（fake OpenAI client + 真 WorkerThread）。
        Day 10: use_fake=True 时跳过真 LLM（截图/单元测试用，不烧 token）。
        """
        if not text or not text.strip():
            return
        if self.isBusy:
            # 真实实现：QML 应已禁用发送按钮；这里做兜底
            self.appendError.emit("system", "正在生成中，请稍候")
            return
        self._send_user_bubble(text.strip())
        self.start_real_chat(text.strip(), use_fake=use_fake)

    @pyqtSlot(str)
    def send_message_mock(self, text: str):
        """Day 1-2 用的 mock 路径（无 WorkerThread，纯 QTimer 推流式）。"""
        if not text or not text.strip():
            return
        self._send_user_bubble(text.strip())
        QTimer.singleShot(500, lambda: self._simulate_ai_reply(text))

    @pyqtSlot(str)
    def set_theme(self, name: str):
        if name in ("light", "dark") and name != self._theme:
            self._theme = name
            self.themeChanged.emit(name)

    @pyqtSlot(result=str)
    def get_theme(self) -> str:
        return self._theme

    @pyqtSlot()
    def stop_chat(self):
        """Day 7: QML 调用的停止生成 - 设停止信号 + 封口当前气泡 + 等线程退出

        不主动 _set_busy(False)，让 QThread.finished -> _on_worker_finished 兑底
        不立即 self._worker = None（避免 race: worker 线程还在跑时已清空）
        """
        if self._worker is None:
            return
        self._worker.stop()
        # Day 7: 当前正在流式的气泡要封口（finalize + flush markdown 渲染）
        if self._last_who:
            self._flush_stream_buffer(self._last_who)
            self.finalizeLast.emit(self._last_who)
            self._last_who = None
        # 等线程退出（最多 2s），防止主线程提前退出导致 Qt 报 "QThread: Destroyed while thread is still running"
        if self._worker.isRunning():
            self._worker.wait(2000)

    # ============ Day 8: 会话管理 Slot ============
    @pyqtSlot(result='QVariantList')
    def list_sessions(self):
        """返回会话列表 [{id, name, time, sel}, ...] - QML 调用填入侧边栏"""
        try:
            convs, current_id = _config.load_conversations()
        except Exception:
            return []
        out = []
        for c in convs:
            out.append({
                "id": c["id"],
                "name": c.get("title") or "新对话",
                "time": self._relative_time_str(c.get("created_at", "")),
                "sel": c["id"] == (current_id or self._current_conv_id),
            })
        return out

    @pyqtSlot(str, result=str)
    def create_session(self, title="新对话"):
        """创建新会话，返回新 id；同时设为当前会话"""
        import uuid as _uuid
        from datetime import datetime as _dt
        new_conv = {
            "id": str(_uuid.uuid4())[:8],
            "title": title or "新对话",
            "history": [],
            "created_at": _dt.now().isoformat(),
        }
        _config.save_single_conversation(new_conv, new_conv["id"])
        self._current_conv_id = new_conv["id"]
        self.sessionListChanged.emit()
        return new_conv["id"]

    @pyqtSlot(str)
    def delete_session(self, conv_id):
        """删除会话；如果删的是当前会话则回退到第一个"""
        try:
            convs, current_id = _config.load_conversations()
        except Exception:
            return
        convs = [c for c in convs if c["id"] != conv_id]
        new_cur = current_id if current_id != conv_id else (convs[0]["id"] if convs else None)
        _config.save_conversations(convs, new_cur)
        if self._current_conv_id == conv_id:
            self._current_conv_id = new_cur
        self.sessionListChanged.emit()

    @pyqtSlot(str)
    def load_session(self, conv_id):
        """加载会话历史 -> emit sessionLoaded(conv_id, history) -> QML 重填 messageModel"""
        try:
            convs, _ = _config.load_conversations()
        except Exception:
            return
        conv = next((c for c in convs if c["id"] == conv_id), None)
        if not conv:
            return
        self._current_conv_id = conv_id
        # history 字段是 [{role: 'user'|'assistant'|'system', content: '...'}]
        history = conv.get("history", []) or []
        self.sessionLoaded.emit(conv_id, history)

    @staticmethod
    def _relative_time_str(iso_str):
        """相对时间文本：今天 HH:MM / 昨天 / N 天前 / YYYY-MM-DD"""
        if not iso_str:
            return ""
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(iso_str)
            now = _dt.now()
            diff = now - dt
            if diff.days == 0:
                return "今天 " + dt.strftime("%H:%M")
            elif diff.days == 1:
                return "昨天"
            elif diff.days < 7:
                return f"{diff.days} 天前"
            else:
                return dt.strftime("%Y-%m-%d")
        except Exception:
            return iso_str[:10]

    # ============ Day 10: 模型切换 Slot ============
    @pyqtSlot(result='QVariantList')
    def list_models(self):
        """Day 10: 返回可选模型列表 [{id, name, current}, ...]"""
        try:
            models, current_id = _config.load_models()
        except Exception:
            return []
        out = []
        for m in models:
            out.append({
                "id": m.get("id", ""),
                "name": m.get("name") or m.get("model_id") or "?",
                "current": m.get("id") == current_id,
            })
        return out

    @pyqtSlot(str, result=bool)
    def set_current_model(self, model_id: str) -> bool:
        """Day 10: 切换当前模型。成功返回 True。"""
        if not model_id:
            return False
        try:
            models, _ = _config.load_models()
            if not any(m.get("id") == model_id for m in models):
                return False
            _config.save_models(models, model_id)
            # 刷新 _last_model_name，后续 start_real_chat 会用新模型
            new_model = next((m for m in models if m.get("id") == model_id), None)
            if new_model:
                self._last_model_name = new_model.get("name") or new_model.get("model_id", "?")
                self.currentModelNameChanged.emit(self._last_model_name)
            return True
        except Exception as e:
            print(f"[chat_bridge] set_current_model 失败: {e}")
            return False

    @pyqtSlot(result=str)
    def get_current_model_name(self) -> str:
        """Day 10: 返回当前模型名称（QML 顶栏显示用）"""
        return self._last_model_name or "(未选择)"

    # ============ 内部辅助 ============
    def _send_user_bubble(self, text: str):
        ts = datetime.now().strftime("%H:%M")
        self.messageAdded.emit("user", text, ts, False, "")
        # Day 10: 用户消息立即写到 SQLite（防止崩溃丢失）
        self._append_history("user", text)

    def _append_history(self, role: str, content: str):
        """Day 10: 把消息 append 到当前会话 history + 写 SQLite

        - user 消息: 立即保存
        - assistant 消息: response_complete / stop / error 时由 _flush_stream_buffer 触发保存
        - 标题自动从第一条 user message 取（前 30 字，"新对话" 时才覆盖）
        """
        if not self._current_conv_id:
            return
        if not content or not content.strip():
            return
        try:
            convs, _ = _config.load_conversations()
            conv = next((c for c in convs if c["id"] == self._current_conv_id), None)
            if conv is None:
                return
            history = conv.get("history") or []
            history.append({"role": role, "content": content})
            conv["history"] = history
            # 标题自动取首条 user 消息前 30 字（仅在"新对话"标题时）
            if role == "user" and conv.get("title", "新对话") == "新对话":
                conv["title"] = (content[:30] + ("..." if len(content) > 30 else "")).strip() or "新对话"
            _config.save_single_conversation(conv, self._current_conv_id)
        except Exception as e:
            print(f"[chat_bridge] 保存历史失败: {e}")


    # ============ Day 3-5: 真实流式（用 fake OpenAI client 跑真 WorkerThread）============
    def start_real_chat(self, user_text: str, use_fake: bool = False):
        """Day 9: 启动一个真 WorkerThread。client 优先从 config 读取：
        - 能读到 api_key/base_url → 用真 LLM则实际发 HTTP 请求。会耗 token。
        - 读不到 / 错误 → 降级到 fake client（验证信号链路用）。

        读取逻辑：
        1. _config.get_current_model() → {api_key, base_url, model_id, proxy, enable_thinking, enable_tools}
        2. api_key 非空 → 生成真 client（会发出去）
        3. 否则 → fake
        """
        client = None
        model_name = "未选择"
        # Day 9+: use_fake=True 时跳过真 LLM（单元测试用，不烧 token）
        if use_fake:
            client = _make_fake_openai_client(user_text)
            self._last_model_name = "(fake)"
        else:
            try:
                current_model = _config.get_current_model()
                if current_model and current_model.get("api_key"):
                    model_name = current_model.get("name") or current_model.get("model_id", "?")
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

        # 真启动 WorkerThread（用空 messages 列表让 worker 不报错）
        self._worker = WorkerThread(
            client=client,
            model_id="mock-qwen",
            enable_thinking=False,
            enable_tools=False,
            messages=[{"role": "user", "content": user_text}],
            plugins={},
            enabled_plugins=[],
            max_rounds=1,
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
        # 启动
        self._set_busy(True)
        # 先发一个空 ai 气泡占位（流式会填进去）
        ts = datetime.now().strftime("%H:%M")
        self._last_who = "ai"
        self.messageAdded.emit("ai", "", ts, False, "")
        self._worker.start()

    def _on_worker_chunk(self, content: str, is_thinking: bool):
        """WorkerThread.chunk_received → QML.appendToLast"""
        who = "thinking" if is_thinking else "ai"
        if self._last_who != who:
            # 切段（思考 → 回答）：新建气泡
            ts = datetime.now().strftime("%H:%M")
            self.messageAdded.emit(who, "", ts, False, "")
            self._last_who = who
        # Day 6: 累积 buffer（流式期间用 PlainText 显示，finalize 时用 Markdown 替换）
        self._stream_buffers[who] = self._stream_buffers.get(who, "") + content
        self.appendToLast.emit(who, content)

    def _on_worker_complete(self, full: str):
        """WorkerThread.response_complete → finalizeLast + 渲染 Markdown → messageReplaced"""
        if self._last_who:
            self.finalizeLast.emit(self._last_who)
            # Day 6: 流式结束后用 markdown_to_html 渲染整段 → 替换气泡为富文本
            self._flush_stream_buffer(self._last_who)
        self._last_who = None

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
        rendered = markdown_to_html(buf, self._theme)
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
        """QThread.finished 兜底：万一 signal 漏了，确保清 busy"""
        self._set_busy(False)
        self._worker = None

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
        # 推到 messageModel（who="tool_call"，text=name，code=args，hasCode=true）
        self.messageAdded.emit("tool_call", name, ts, True, args_display)
        self.toolCallStarted.emit(name, args_str)

    def _on_worker_tool_call_result(self, name: str, args_str: str, result: str):
        """WorkerThread.tool_call_result → QML 显示工具结果气泡

        推到 messageModel（who="tool_result"，text=name，code=result）
        """
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        # 截断过长结果（避免气泡爆长）
        result_display = result if len(result) <= 800 else (result[:800] + "\n... (已截断)")
        self.messageAdded.emit("tool_result", name, ts, True, result_display)
        self.toolCallResult.emit(name, args_str, result)

    # ============ Day 1-2 兼容：旧 mock 路径（保留但默认不用）============
    def _simulate_ai_reply(self, prompt: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        self._last_who = "ai"
        self.messageAdded.emit("ai", "", ts, False, "")
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
