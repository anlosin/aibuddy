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
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer

from .worker import WorkerThread


class ChatBridge(QObject):

    # ============ Python → QML 信号 ============
    messageAdded = pyqtSignal(str, str, str, bool, str)   # who, text, ts, hasCode, code
    appendToLast = pyqtSignal(str, str)                    # who, content
    finalizeLast = pyqtSignal(str)                          # who
    appendError = pyqtSignal(str, str)                      # who, text
    themeChanged = pyqtSignal(str)                          # "light" | "dark"

    # ============ 内部状态（暴露给测试/QML 读）============
    busyChanged = pyqtSignal(bool)                          # 是否正在生成中（可让发送按钮变停）
    isBusy = False

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self._theme = theme
        self._last_who = None      # 当前正在流式的气泡 who
        self._stream_timer = None    # 防止短时间重复 emit（节流）

    # ============ QML → Python Slot ============
    @pyqtSlot(str)
    def send_message(self, text: str):
        """QML 触发用户发送一条消息。

        Day 3-5 起默认走 real 路径（fake OpenAI client + 真 WorkerThread）。
        留 _use_mock 槽位便于 Day 1-2 验证。
        """
        if not text or not text.strip():
            return
        if self.isBusy:
            # 真实实现：QML 应已禁用发送按钮；这里做兜底
            self.appendError.emit("system", "正在生成中，请稍候")
            return
        self._send_user_bubble(text.strip())
        self.start_real_chat(text.strip())

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
        """QML 调用的停止生成（暂未实现，Day 6+ 补）"""
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
            self._set_busy(False)

    # ============ 内部辅助 ============
    def _send_user_bubble(self, text: str):
        ts = datetime.now().strftime("%H:%M")
        self.messageAdded.emit("user", text, ts, False, "")

    def _set_busy(self, busy: bool):
        if busy != self.isBusy:
            self.isBusy = busy
            self.busyChanged.emit(busy)

    # ============ Day 3-5: 真实流式（用 fake OpenAI client 跑真 WorkerThread）============
    def start_real_chat(self, user_text: str):
        """启动一个真 WorkerThread，client 是 fake 的（不发真请求）。

        这样能验证：
        1. WorkerThread 信号（chunk_received/response_complete/error_occurred）能正确桥到 QML
        2. 流式 chunk 累积、节流、错误处理在真线程里工作
        3. 取消/重入保护
        """
        # Day 3-5 阶段：默认用 fake client（不烧 token / 不依赖真 API）
        # Day 6+ 接真 client 时把下面这行换成 config.make_openai_client(...)
        client = _make_fake_openai_client(user_text)

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
        self.appendToLast.emit(who, content)

    def _on_worker_complete(self, full: str):
        """WorkerThread.response_complete → QML.finalizeLast"""
        if self._last_who:
            self.finalizeLast.emit(self._last_who)
        self._last_who = None

    def _on_worker_error(self, msg: str):
        """WorkerThread.error_occurred → QML.appendError"""
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        self.appendError.emit("error", f"{msg} ({ts})")
        # 错误时收尾（解 busy、清最后气泡）
        if self._last_who:
            self.finalizeLast.emit(self._last_who)
            self._last_who = None

    def _on_worker_finished(self):
        """QThread.finished 兜底：万一 signal 漏了，确保清 busy"""
        self._set_busy(False)
        self._worker = None

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

    # 把 user_text 拆成几个 chunk，模拟流式
    text = (
        f"你说：{user_text}\n\n"
        "这是 fake OpenAI client 模拟的流式响应（Day 3-5）。\n\n"
        "**验证通过**：\n"
        "1. WorkerThread 启动成功\n"
        "2. chunk_received 逐个 emit\n"
        "3. ChatBridge 桥接到 QML\n"
        "4. QML 端 appendToLast 实时追加"
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
