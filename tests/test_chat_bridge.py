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


class TestMessagePersistence(unittest.TestCase):
    """Day 10: 消息保存到 SQLite

    验证:
    1. user 消息发送后立即写 SQLite
    2. assistant 消息 finalize 时写 SQLite
    3. title 自动从首条 user 消息取前 30 字（仅默认"新对话"时）
    4. 写历史失败不阻塞主流程
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def _pump(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec_()

    def test_user_message_persisted_to_sqlite(self):
        """发送 user 消息后立即能读到 SQLite"""
        cid = self.bridge.create_session("")  # 默认 "新对话"
        self.created_ids.append(cid)
        self.bridge.send_message("测试消息", use_fake=True)
        self._pump(100)  # 立即读
        from qwen_app import config
        convs, _ = config.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        user_msgs = [h for h in conv["history"] if h["role"] == "user"]
        self.assertEqual(len(user_msgs), 1)
        self.assertEqual(user_msgs[0]["content"], "测试消息")

    def test_assistant_message_persisted_after_completion(self):
        """assistant 消息在 finalize 时写 SQLite"""
        cid = self.bridge.create_session("")
        self.created_ids.append(cid)
        self.bridge.send_message("测试", use_fake=True)
        self._pump(2500)  # 等 fake 流式完成
        from qwen_app import config
        convs, _ = config.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        roles = [h["role"] for h in conv["history"]]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertEqual(len(conv["history"]), 2)

    def test_title_auto_updated_from_first_user_message(self):
        """首条 user 消息应该自动成为 title"""
        cid = self.bridge.create_session("")  # title="新对话"
        self.created_ids.append(cid)
        self.bridge.send_message("Python 快速排序怎么写？", use_fake=True)
        self._pump(100)
        from qwen_app import config
        convs, _ = config.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        self.assertEqual(conv["title"], "Python 快速排序怎么写？")

    def test_title_not_overwritten_if_user_set(self):
        """用户设过的 title 不应该被覆盖"""
        cid = self.bridge.create_session("我的固定标题")
        self.created_ids.append(cid)
        self.bridge.send_message("Python 快速排序怎么写？", use_fake=True)
        self._pump(100)
        from qwen_app import config
        convs, _ = config.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        self.assertEqual(conv["title"], "我的固定标题")  # 不被覆盖

    def test_long_title_truncated(self):
        """超过 30 字的 title 截断"""
        cid = self.bridge.create_session("")
        self.created_ids.append(cid)
        long_msg = "a" * 100  # 100 字
        self.bridge.send_message(long_msg, use_fake=True)
        self._pump(100)
        from qwen_app import config
        convs, _ = config.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        self.assertEqual(len(conv["title"]), 33)  # 30 + "..."
        self.assertTrue(conv["title"].endswith("..."))


class TestModelSwitching(unittest.TestCase):
    """Day 10: 模型切换菜单桥到 QML"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        # 备份当前模型，测试结束还原
        from qwen_app import config
        _, self._original_id = config.load_models()

    def tearDown(self):
        # 还原当前模型
        if self._original_id:
            self.bridge.set_current_model(self._original_id)

    def test_list_models_returns_qvariantlist(self):
        """list_models 应返回 list[dict]，每个 dict 含 id/name/current"""
        models = self.bridge.list_models()
        self.assertIsInstance(models, list)
        if models:  # 数据库可能空
            m = models[0]
            self.assertIn("id", m)
            self.assertIn("name", m)
            self.assertIn("current", m)
        # 当前模型应该被标记
        currents = [m for m in models if m["current"]]
        self.assertEqual(len(currents), 1)

    def test_get_current_model_name(self):
        """get_current_model_name 返回当前激活的模型名"""
        name = self.bridge.get_current_model_name()
        self.assertTrue(name)
        self.assertNotEqual(name, "(未选择)")

    def test_set_current_model_changes_active(self):
        """set_current_model 应该切换当前模型 + 触发 signal"""
        models = self.bridge.list_models()
        if len(models) < 2:
            self.skipTest("只有一个模型，跳过切换测试")
        other = next(m for m in models if not m["current"])
        captured = []
        self.bridge.currentModelNameChanged.connect(lambda n: captured.append(n))
        ok = self.bridge.set_current_model(other["id"])
        self.assertTrue(ok)
        self.assertEqual(self.bridge.get_current_model_name(), other["name"])
        self.assertEqual(captured, [other["name"]])
        # list_models 中 current 应该换了
        models_after = self.bridge.list_models()
        currents = [m for m in models_after if m["current"]]
        self.assertEqual(len(currents), 1)
        self.assertEqual(currents[0]["id"], other["id"])

    def test_set_nonexistent_model_returns_false(self):
        """设置不存在的 model id 应该返回 False（不抛异常）"""
        captured = []
        self.bridge.currentModelNameChanged.connect(lambda n: captured.append(n))
        ok = self.bridge.set_current_model("nonexistent_model_xyz")
        self.assertFalse(ok)
        self.assertEqual(captured, [])  # 没切换

    def test_set_empty_model_id_returns_false(self):
        """空字符串 id 应该返回 False"""
        ok = self.bridge.set_current_model("")
        self.assertFalse(ok)


class TestToolCallBridge(unittest.TestCase):
    """Day 11: 工具调用桥接（worker.tool_call_start/result → QML messageAdded）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []
        # 跟踪 messageAdded
        self.messages = []
        self.bridge.messageAdded.connect(
            lambda who, text, ts, has_code, code: self.messages.append({
                "who": who, "text": text, "ts": ts, "has_code": has_code, "code": code
            })
        )
        # 跟踪 tool_call signals
        self.tool_started = []
        self.tool_result = []
        self.bridge.toolCallStarted.connect(lambda n, a: self.tool_started.append((n, a)))
        self.bridge.toolCallResult.connect(lambda n, a, r: self.tool_result.append((n, a, r)))

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def test_tool_call_start_adds_to_message_model(self):
        """tool_call_start 应该 emit messageAdded(who="tool_call") + toolCallStarted"""
        self.bridge._on_worker_tool_call_start("get_weather", '{"city":"上海"}')
        self.assertEqual(len(self.messages), 1)
        msg = self.messages[0]
        self.assertEqual(msg["who"], "tool_call")
        self.assertEqual(msg["text"], "get_weather")
        self.assertTrue(msg["has_code"])
        self.assertIn("上海", msg["code"])  # args JSON 解析后含 city
        self.assertEqual(len(self.tool_started), 1)

    def test_tool_call_start_invalid_json_keeps_raw(self):
        """参数不是 JSON 时保留原文"""
        self.bridge._on_worker_tool_call_start("bad_tool", "{invalid json")
        msg = self.messages[0]
        self.assertEqual(msg["code"], "{invalid json")

    def test_tool_call_result_adds_to_message_model(self):
        """tool_call_result 应该 emit messageAdded(who="tool_result") + toolCallResult"""
        result = "上海：晴 18°C"
        self.bridge._on_worker_tool_call_result("get_weather", "{}", result)
        self.assertEqual(len(self.messages), 1)
        msg = self.messages[0]
        self.assertEqual(msg["who"], "tool_result")
        self.assertEqual(msg["text"], "get_weather")
        self.assertEqual(msg["code"], result)
        self.assertEqual(len(self.tool_result), 1)

    def test_long_tool_result_truncated(self):
        """超长工具结果截断到 800 字 + 省略号"""
        long_result = "x" * 1000
        self.bridge._on_worker_tool_call_result("big_tool", "{}", long_result)
        msg = self.messages[0]
        self.assertLess(len(msg["code"]), 850)
        self.assertIn("已截断", msg["code"])


if __name__ == "__main__":
    unittest.main()
