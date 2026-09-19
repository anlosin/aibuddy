"""Day 20.4.3: 消息渲染预处理器测试。

背景：用户报「对话的气泡里的内容完全看不见」。

根因：QML MessageBubble.qml 用 textFormat: Text.RichText 显示 text，但
bridge 直接 emit 原始内容：
- user 原始文本含 ``<`` / ``&`` 时 RichText 解析失败 → 整段消失
- AI history reload 时没经 markdown_to_html → markdown 不渲染
- tool_call 的 JSON 参数含 ``<tool>`` 之类 → RichText 误读成标签

修复：bridge 在 emit 前过 _render_for_qml：
- user → html.escape
- assistant → markdown_to_html
- tool_call / tool_result / system → html.escape（不实际用，但统一处理）

Day 20.6.19: setUp 调 set_db_path_for_tests 把数据库重定向到 tmpdir，
避免污染生产 conversations.db。tearDown 还原。
"""
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestRenderForQml(unittest.TestCase):
    """_render_for_qml 必须正确处理三种角色"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")

    def test_user_text_escaped(self):
        """user 文本必须 escape（防 RichText 误读）"""
        out = self.bridge._render_for_qml("user", "use <stdio.h>", "light")
        self.assertNotIn("<stdio.h>", out,
                         "<stdio.h> 不应原文出现（RichText 会误读成标签）")
        self.assertIn("&lt;stdio.h&gt;", out, "应该 escape 成 &lt; &gt;")

    def test_user_text_amp_escaped(self):
        """& 必须 escape"""
        out = self.bridge._render_for_qml("user", "A & B", "light")
        self.assertNotIn("A & B", out)
        self.assertIn("A &amp; B", out)

    def test_user_plain_text_unchanged(self):
        """纯文本应该原样保留（除了转义）"""
        out = self.bridge._render_for_qml("user", "hello world", "light")
        self.assertEqual(out, "hello world")

    def test_assistant_markdown_rendered(self):
        """assistant 文本走 markdown → HTML（**bold** → <b>bold</b>）"""
        out = self.bridge._render_for_qml("assistant", "**bold** text", "light")
        self.assertIn("<b>bold</b>", out, "markdown ** 应转成 <b>（项目用 QTextEdit 风格）")
        self.assertNotIn("**bold**", out, "** 不应原文出现")

    def test_assistant_code_block_rendered(self):
        """代码块应转成 <pre ...>（带 style 是预期）"""
        out = self.bridge._render_for_qml(
            "assistant", "```python\nprint('hi')\n```", "light")
        self.assertRegex(out, r"<pre[^>]*>", "必须有 <pre 标签")
        self.assertIn("print", out)

    def test_assistant_code_with_lt_safe(self):
        """AI 回复含 ``<stdio.h>`` 时应正确转义 + 显示"""
        out = self.bridge._render_for_qml(
            "assistant", "use `#include <stdio.h>`", "light")
        # 行内代码 ``...`` 应被 markdown 保留（但 escape <）
        self.assertIn("stdio.h", out)
        self.assertNotIn("use `<stdio.h>`", out, "原始 < 不应保留在 RichText 输出里")

    def test_tool_call_escaped(self):
        """tool_call 的 name 应 escape（PlainText 路径下也安全）"""
        out = self.bridge._render_for_qml(
            "tool_call", "<dangerous>", "light")
        self.assertIn("&lt;dangerous&gt;", out)

    def test_tool_result_escaped(self):
        """tool_result 的 result 应 escape"""
        out = self.bridge._render_for_qml(
            "tool_result", "result: 1 < 2", "light")
        self.assertIn("&lt;", out)

    def test_system_escaped(self):
        """system 消息也 escape（防御性）"""
        out = self.bridge._render_for_qml(
            "system", "init <fail>", "light")
        self.assertIn("&lt;fail&gt;", out)

    def test_empty_content_returns_empty(self):
        """空内容 → 空串"""
        self.assertEqual(self.bridge._render_for_qml("user", "", "light"), "")
        self.assertEqual(self.bridge._render_for_qml("assistant", None, "light"), "")


class TestLoadSessionPreRendersHistory(unittest.TestCase):
    """load_session 必须把 history 全部过 _render_for_qml 后再 emit"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app import config as _cfg
        # 隔离 db 到 tmpdir（Day 20.6.19）
        self._tmpdir = tempfile.mkdtemp(prefix="day20_4_3_render_")
        _cfg.set_db_path_for_tests(os.path.join(self._tmpdir, "conversations.db"))
        self.bridge = ChatBridge(theme="light")
        self._cfg = _cfg
        # 准备一条带 user + AI + tool_call 的会话
        self.conv_id = "day20_4_3_test_render"
        _cfg.save_single_conversation({
            "id": self.conv_id,
            "title": "渲染测试",
            "history": [
                {"role": "user", "content": "use <stdio.h>"},
                {"role": "assistant", "content": "**bold** answer"},
                {"role": "tool_call", "content": "calculator", "code": "1+1"},
                {"role": "tool_result", "content": "result: 2"},
            ],
            "created_at": "2026-09-14T00:00:00",
        }, self.conv_id)
        self.loaded = []
        self.bridge.sessionLoaded.connect(
            lambda cid, hist: self.loaded.append((cid, hist)))

    def tearDown(self):
        try:
            self._cfg.close_all_conns()
        except Exception:
            pass
        self._cfg.set_db_path_for_tests(None)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_user_history_escaped(self):
        """history 中的 user 文本必须 escape"""
        self.bridge.load_session(self.conv_id)
        _, history = self.loaded[-1]
        user_msg = next(h for h in history if h["role"] == "user")
        self.assertIn("&lt;stdio.h&gt;", user_msg["content"])
        self.assertNotIn("<stdio.h>", user_msg["content"])

    def test_assistant_history_markdown_rendered(self):
        """history 中的 assistant 文本必须 markdown 渲染"""
        self.bridge.load_session(self.conv_id)
        _, history = self.loaded[-1]
        ai_msg = next(h for h in history if h["role"] == "assistant")
        self.assertIn("<b>bold</b>", ai_msg["content"])
        self.assertNotIn("**bold**", ai_msg["content"])

    def test_load_session_does_not_double_escape(self):
        """load_session 后内容不能双重 escape"""
        # 如果 _render_for_qml 被调两次，``<`` 会变成 ``&amp;lt;``
        self.bridge.load_session(self.conv_id)
        _, history = self.loaded[-1]
        user_msg = next(h for h in history if h["role"] == "user")
        # 不应有 &amp;lt;
        self.assertNotIn("&amp;lt;", user_msg["content"])


class TestSendUserBubblePreRenders(unittest.TestCase):
    """_send_user_bubble emit 的 text 必须 escape"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        # 隔离 db 到 tmpdir（Day 20.6.19）
        from qwen_app import config as _cfg
        self._tmpdir = tempfile.mkdtemp(prefix="day20_4_3_bubble_")
        _cfg.set_db_path_for_tests(os.path.join(self._tmpdir, "conversations.db"))
        self._cfg = _cfg
        self.bridge = ChatBridge(theme="light")
        self.added = []
        self.bridge.messageAdded.connect(
            lambda who, payload: self.added.append((who, payload)))

    def tearDown(self):
        try:
            self._cfg.close_all_conns()
        except Exception:
            pass
        self._cfg.set_db_path_for_tests(None)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_user_text_with_lt_escaped(self):
        """emit 给 QML 的 user text 必须 escape"""
        self.bridge._send_user_bubble("use <stdio.h>")
        who, payload = self.added[-1]
        self.assertEqual(who, "user")
        self.assertIn("&lt;stdio.h&gt;", payload["text"])
        self.assertNotIn("<stdio.h>", payload["text"])

    def test_user_text_amp_escaped(self):
        self.bridge._send_user_bubble("A & B")
        _, payload = self.added[-1]
        self.assertIn("&amp; B", payload["text"])

    def test_history_stores_raw_text(self):
        """存盘还是 raw text（QML 已 escape 过，存盘不需要再 escape）"""
        # 这是设计选择：_append_history 第二个参数是 raw text，
        # 存盘保留 raw 是为了 reload 时能再次 escape / render
        # （避免双重 escape）
        _cfg = self._cfg
        # 准备一个空会话
        test_id = "day20_4_3_userbubble"
        _cfg.save_single_conversation({
            "id": test_id, "title": "t",
            "history": [], "created_at": "2026-09-14T00:00:00",
        }, test_id)
        try:
            self.bridge._current_conv_id = test_id
            self.bridge._send_user_bubble("raw <text>")
            # 重新读盘验证存的还是 raw
            convs, _ = _cfg.load_conversations()
            target = next(c for c in convs if c["id"] == test_id)
            last = target["history"][-1]
            self.assertEqual(last["content"], "raw <text>",
                             "存盘必须保留 raw（不能 escape）")
        finally:
            convs, cur = _cfg.load_conversations()
            convs = [c for c in convs if c.get("id") != test_id]
            _cfg.save_conversations(convs, cur)


if __name__ == "__main__":
    unittest.main()
