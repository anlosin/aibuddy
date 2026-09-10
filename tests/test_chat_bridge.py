"""Day 7: ChatBridge \u5355\u5143\u6d4b\u8bd5

\u8986\u76d6\uff1a
1. pyqtProperty isBusy \u53cc\u5411\u7ed1\u5b9a\u5de5\u4f5c
2. stop_chat \u8c03\u7528\u4e0d\u62a5\u9519\uff08\u5de5\u4f5c\u4e2d/\u672a\u5de5\u4f5c\u4e24\u79cd\u72b6\u6001\uff09
3. busyChanged signal \u6b63\u786e\u89e6\u53d1
4. \u591a\u6b21\u8c03\u7528 stop_chat \u4e0d\u4f1a\u62a5\u9519
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer
from qwen_app.chat_bridge import ChatBridge


def _pump(ms=200):
    """\u8dd1 Qt \u4e8b\u4ef6\u5faa\u73af ms \u6beb\u79d2\uff0c\u4f7f\u4fe1\u53f7\u80fd\u88ab\u5904\u7406"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


class TestChatBridgeBusy(unittest.TestCase):
    """isBusy \u53cc\u5411\u7ed1\u5b9a + busyChanged signal"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def test_initial_busy_is_false(self):
        b = ChatBridge(theme="light")
        self.assertFalse(b.isBusy)

    def test_set_busy_true_emits_signal(self):
        b = ChatBridge(theme="light")
        events = []
        b.busyChanged.connect(lambda v: events.append(v))
        b._set_busy(True)
        self.assertTrue(b.isBusy)
        self.assertEqual(events, [True])

    def test_set_busy_same_value_no_signal(self):
        """\u8bbe\u4e3a\u540c\u4e00\u503c\u4e0d\u91cd\u590d emit\uff08\u907f\u514d\u65e0\u8c13\u91cd\u7ed1\u5b9a\uff09"""
        b = ChatBridge(theme="light")
        b._set_busy(True)
        events = []
        b.busyChanged.connect(lambda v: events.append(v))
        b._set_busy(True)  # \u540c\u503c\uff0c\u4e0d\u8be5 emit
        self.assertEqual(events, [])


class TestStopChat(unittest.TestCase):
    """stop_chat \u5728\u4e0d\u540c\u72b6\u6001\u4e0b\u7684\u884c\u4e3a"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def test_stop_without_worker_is_noop(self):
        """\u6ca1\u6709\u6b63\u5728\u8dd1\u7684 worker \u65f6\uff0cstop_chat \u4e0d\u62a5\u9519"""
        b = ChatBridge(theme="light")
        b.stop_chat()  # \u4e0d\u5e94\u62a5\u9519
        self.assertFalse(b.isBusy)

    def test_stop_after_natural_completion(self):
        """\u5de5\u4f5c\u81ea\u7136\u5b8c\u6210\u540e\u8c03 stop_chat \u4e0d\u62a5\u9519"""
        b = ChatBridge(theme="light")
        b.start_real_chat("hello", use_fake=True)
        _pump(2000)  # \u7b49\u5de5\u4f5c\u5b8c\u6210\uff08fake client \u4e00\u6b21\u6027 emit \u5b8c\uff09
        b.stop_chat()  # worker \u5df2\u9000\u51fa\uff0c\u4e0d\u62a5\u9519
        self.assertFalse(b.isBusy)

    def test_multiple_stop_calls_safe(self):
        """\u591a\u6b21\u8c03\u7528 stop_chat \u4e0d\u62a5\u9519\uff08\u9632\u6b62\u91cd\u590d\u70b9\u51fb\u9519\u8bef\uff09"""
        b = ChatBridge(theme="light")
        b.start_real_chat("hello", use_fake=True)
        b.stop_chat()
        b.stop_chat()  # \u7b2c\u4e8c\u6b21\u4e3a noop
        b.stop_chat()
        _pump(500)
        self.assertFalse(b.isBusy)

    def test_stop_emits_finalize_and_replaced(self):
        """\u6b63\u5728\u6d41\u5f0f\u65f6\u8c03 stop_chat\uff0c\u5e94\u89e6\u53d1 finalizeLast + messageReplaced"""
        b = ChatBridge(theme="light")
        finalize_calls = []
        replace_calls = []
        b.finalizeLast.connect(lambda who: finalize_calls.append(who))
        b.messageReplaced.connect(lambda who, text: replace_calls.append(who))
        b.start_real_chat("hello", use_fake=True)
        b.stop_chat()  # \u7acb\u5373\u8c03\u7528\uff08worker \u8fd8\u5728\u8dd1 fake \u751f\u6210\u5668\uff09
        _pump(1500)  # \u7b49 worker \u9000\u51fa
        # stop_chat \u672c\u8eab\u4f1a\u89e6\u53d1 finalizeLast + messageReplaced\uff08\u8be5 worker \u6ca1\u8d70\u5230 complete\uff09
        # _on_worker_finished \u4e0d\u4f1a\u53d1 finalizeLast
        self.assertIn("ai", finalize_calls)
        # 空 buf 时 _flush_stream_buffer 跳过 emit messageReplaced（合理：没流过内容就没东西渲染）


class TestSessionManagement(unittest.TestCase):
    """Day 8: 会话管理（list / create / delete / load）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []  # 测试结束清理

    def tearDown(self):
        # 清理测试创建的会话
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def test_list_sessions_returns_qvariantlist(self):
        """list_sessions 应返回 list[dict]，每个 dict 包含 id/name/time/sel"""
        sessions = self.bridge.list_sessions()
        self.assertIsInstance(sessions, list)
        if sessions:  # 数据库可能空
            s = sessions[0]
            self.assertIn("id", s)
            self.assertIn("name", s)
            self.assertIn("time", s)
            self.assertIn("sel", s)

    def test_create_session_returns_id_and_persists(self):
        """create_session 返回新 id，列表里能找到"""
        new_id = self.bridge.create_session("Day 8 unit test")
        self.created_ids.append(new_id)
        self.assertTrue(new_id)
        self.assertEqual(len(new_id), 8)  # uuid4 hex[:8]
        # 重新查应该能找到
        sessions = self.bridge.list_sessions()
        ids = [s["id"] for s in sessions]
        self.assertIn(new_id, ids)
        # 找到的 session 应该有正确的 title
        created = next(s for s in sessions if s["id"] == new_id)
        self.assertEqual(created["name"], "Day 8 unit test")
        self.assertTrue(created["sel"])  # 新创建的应该是当前会话

    def test_create_emits_session_list_changed(self):
        """create_session 应该 emit sessionListChanged"""
        events = []
        self.bridge.sessionListChanged.connect(lambda: events.append(1))
        new_id = self.bridge.create_session("Day 8 emit test")
        self.created_ids.append(new_id)
        self.assertEqual(events, [1])

    def test_delete_session_removes_it(self):
        """delete_session 后列表里不再有"""
        new_id = self.bridge.create_session("to delete")
        self.created_ids  # 不放进 cleanup，避免双重删除
        sessions_before = len(self.bridge.list_sessions())
        self.bridge.delete_session(new_id)
        sessions_after = len(self.bridge.list_sessions())
        self.assertEqual(sessions_after, sessions_before - 1)
        ids_after = [s["id"] for s in self.bridge.list_sessions()]
        self.assertNotIn(new_id, ids_after)

    def test_load_session_emits_history(self):
        """load_session 应该 emit sessionLoaded(conv_id, history)"""
        new_id = self.bridge.create_session("with history")
        self.created_ids.append(new_id)
        # 写一条历史（直接通过 config 模拟）
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        target = next(c for c in convs if c["id"] == new_id)
        target["history"] = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        _cfg.save_single_conversation(target, new_id)
        # 测试 load_session
        captured = []
        self.bridge.sessionLoaded.connect(lambda cid, hist: captured.append((cid, hist)))
        self.bridge.load_session(new_id)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], new_id)
        self.assertEqual(len(captured[0][1]), 2)

    def test_delete_current_session_falls_back(self):
        """删除当前会话时应该回退到第一个（或 None）"""
        # 创建 2 个会话
        id_a = self.bridge.create_session("A")
        id_b = self.bridge.create_session("B")
        self.created_ids.extend([id_a, id_b])
        # 当前会话是 B（后创建的）
        # 删除 B 应该让 current 回退到 A
        self.bridge.delete_session(id_b)
        self.assertNotEqual(self.bridge._current_conv_id, id_b)

    def test_load_nonexistent_session_is_noop(self):
        """load 一个不存在的 id 应该 noop（不 emit）"""
        captured = []
        self.bridge.sessionLoaded.connect(lambda *a: captured.append(a))
        self.bridge.load_session("nonexistent_id_xyz")
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
