"""ChatBridge — Python ↔ QML 双向通信桥（阶段 2 Day 1-2 骨架）。

暴露给 QML 的 5 个核心能力（最小可用集）：
1. send_message(text)  — QML 触发，Python 模拟产生 user/ai 消息
2. set_theme(name)      — QML 切换主题
3. append_chunk / finalize_last / report_error — Python 流式信号回调
4. message_added / append_to_last / finalize_last / append_error / theme_changed
   — Python 主动发往 QML 的信号

不在本骨架范围（Day 3-5+ 才做）：
- 接真 worker.py / 流式 chunk 路由
- 工具调用 UI（handle_tool_call_*）
- 真实会话历史加载（save_current_to_conv 等）
- Markdown 完整渲染（先用主题.py build_bubble 的渲染结果当 QML 文本）
"""
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer


class ChatBridge(QObject):
    """桥接器：把 PyQt5 后端的语义（消息 / 流 / 主题）翻译成 QML 可消费的信号。"""

    # ============ Python → QML（Signal）============
    # 一条完整消息已加入气泡列表
    messageAdded = pyqtSignal(str, str, str, bool, str)   # who, text, ts, hasCode, code
    # 流式追加到最后一个气泡
    appendToLast = pyqtSignal(str, str)                    # who, content
    # 标记最后一个气泡完成（关闭光标闪烁 / 解锁输入）
    finalizeLast = pyqtSignal(str)                          # who
    # 错误气泡（不入主消息流）
    appendError = pyqtSignal(str, str)                      # who, text
    # 主题切换（QML 端 reactive 重新渲染）
    themeChanged = pyqtSignal(str)                          # "light" | "dark"

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self._theme = theme
        # 流式模拟用：保存"最后一个气泡的 who"，让 appendToLast 能定位
        self._last_who = None

    # ============ QML → Python（Slot）============
    @pyqtSlot(str)
    def send_message(self, text: str):
        """用户发送一条消息：先渲染 user 气泡，3 秒后模拟 AI 流式回复。

        真实实现应在 Day 3-5 替换为调用 worker_thread.start_chat。
        """
        if not text or not text.strip():
            return
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        # 1) user 气泡
        self.messageAdded.emit("user", text.strip(), ts, False, "")
        # 2) 3 秒后开始 AI 流式
        QTimer.singleShot(500, lambda: self._simulate_ai_reply(text))

    @pyqtSlot(str)
    def set_theme(self, name: str):
        if name in ("light", "dark") and name != self._theme:
            self._theme = name
            self.themeChanged.emit(name)

    @pyqtSlot(result=str)
    def get_theme(self) -> str:
        return self._theme

    # ============ Python 内部（真实流式用）============
    def on_chunk(self, content: str, is_thinking: bool):
        """worker 的流式 chunk 回调。Day 3-5 会接真 worker。"""
        who = "thinking" if is_thinking else "ai"
        if self._last_who != who:
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M")
            self.messageAdded.emit(who, "", ts, False, "")
            self._last_who = who
        self.appendToLast.emit(who, content)

    def on_complete(self):
        """worker 的流式完成回调。"""
        if self._last_who:
            self.finalizeLast.emit(self._last_who)
            self._last_who = None

    def on_error(self, msg: str):
        """worker 的错误回调。"""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        self.appendError.emit("error", f"错误: {msg} ({ts})")

    # ============ mock：模拟 AI 流式回复（仅 Day 1-2 测试用）============
    def _simulate_ai_reply(self, prompt: str):
        """模拟一个流式 AI 回复：3 段文本，间隔 600ms。

        Day 3-5 替换为真实 worker_thread.start_chat()。
        """
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        self._last_who = "ai"
        self.messageAdded.emit("ai", "", ts, False, "")
        replies = [
            f"我理解你问的是：{prompt[:30]}...",
            "这是来自 QML 桥接的模拟回复。\n\n真实流式响应会在 Day 3-5 接上 worker。",
            "如果你看到这条，说明桥接工作正常 ✓"
        ]
        # 第一段立即推
        self.appendToLast.emit("ai", replies[0])
        # 后续段定时推
        QTimer.singleShot(600, lambda: self._push_chunk(replies[1]))
        QTimer.singleShot(1200, lambda: self._push_chunk(replies[2]))
        QTimer.singleShot(1500, self._finish_simulate)

    def _push_chunk(self, content: str):
        self.appendToLast.emit("ai", "\n\n" + content)

    def _finish_simulate(self):
        self.finalizeLast.emit("ai")
        self._last_who = None
