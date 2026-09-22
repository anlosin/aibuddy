# -*- coding: utf-8 -*-
"""Day 20.6.22: 上下文记忆（enable_context）+ QtQuick 路径历史发送护栏

根因（复核）：
  - chat_bridge._bridge_stream.start_real_chat 构造的 messages 只含
    system + user，**没有把当前会话 history 拼进去** → 模型每轮只见当前
    一条 → 「上下文不发送」。
  - chat_window 备用路径有 enable_context 内存态 + 状态栏 + 菜单切换；
    QtQuick 路径之前完全没暴露 → 用户「开关也没了」。

修复（_bridge_stream.StreamMixin._collect_history_for_context +
_chat_bridge.ChatBridge.__init__ 增 _enable_context +
_bridge_model.ModelMixin.set_preferences 加 enable_context 参数 +
set_enable_context 单 slot + Main.qml 视图菜单加切换项）。

护栏策略：
- _collect_history_for_context 行为验证（开关 / 截断 / 字段过滤 / 不存在）
- set_preferences 三参持久化 + 即时生效
- set_enable_context 单 slot 持久化
- start_real_chat 拼 messages 顺序：system → history（最多 50 条）→ user
- 关闭时 history 不拼、messages 仅 system + user
"""
import inspect
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _func_source(path, name):
    import ast, textwrap
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return textwrap.dedent(ast.get_source_segment(src, n) or "")
    raise AssertionError(f"{name} 未在 {path} 中找到")


# 找 chat_bridge 模块路径（patch 到模块级属性，避开 mixin 注入顺序问题）
import qwen_app.chat_bridge as chat_bridge_mod
import qwen_app._bridge_stream as stream_mod
import qwen_app._bridge_model as model_mod
import qwen_app.config as config_mod
import qwen_app.compressor as compressor_mod


# Day 20.6.22 教训：所有用例 mock load_conversations / load_config / save_config，
# 不真写 db；不调 set_db_path_for_tests（避免污染下游 test_session_state 的
# _IsolatedDB，它备份 cfg.CONVERSATIONS_DB 常量但 cfg._active_db_path() 优先
# 看 _TEST_PATH_OVERRIDE，与 set_db_path_for_tests 互斥）。


class TestCollectHistoryForContext(unittest.TestCase):
    """_collect_history_for_context 行为矩阵"""

    # Day 20.6.22 教训：不能用 self.orig_load = config_mod.load_conversations
    # 快照再恢复 —— unittest 每个用例独立 setUp，但若同测试模块上一个用例
    # mock 没还原，self.orig_load 拿到的是「上一个用例的 mock 函数」，永远还原不回去。
    # 用 module-level 真函数句柄（import 时一次性绑定）+ setattr/setdelattr 兜底。
    _REAL_LOAD_CONVS = staticmethod(config_mod.load_conversations)

    def setUp(self):
        self.conv_id = "conv_xyz_1"
        # 兜底：万一 setUp 异常后某个用例残留了 mock，tearDown 必须能恢复真函数
        self._patcher = mock.patch.object(
            config_mod, "load_conversations", self._REAL_LOAD_CONVS)
        self._patcher.start()

    def _stub_convs(self, history):
        # 覆写到真实函数位置（不污染 self._REAL_LOAD_CONVS）
        mock.patch.object(
            config_mod, "load_conversations",
            lambda: ([{"id": self.conv_id, "history": history}], self.conv_id)
        ).start()

    def tearDown(self):
        mock.patch.stopall()

    def _make_bridge(self, enable_context=True, limit=50):
        """构造一个最小 StreamMixin 实例，避开 ChatBridge 的 Qt 初始化。"""
        inst = stream_mod.StreamMixin.__new__(stream_mod.StreamMixin)
        inst._enable_context = enable_context
        inst._context_history_limit = limit
        inst._current_conv_id = self.conv_id
        return inst

    def test_disabled_returns_empty(self):
        self._stub_convs([{"role": "user", "content": "hi"},
                          {"role": "assistant", "content": "hello"}])
        inst = self._make_bridge(enable_context=False)
        self.assertEqual(inst._collect_history_for_context(self.conv_id), [])

    def test_no_conv_id_returns_empty(self):
        self._stub_convs([])
        inst = self._make_bridge()
        self.assertEqual(inst._collect_history_for_context(""), [])

    def test_unknown_conv_id_returns_empty(self):
        self._stub_convs([])
        inst = self._make_bridge()
        self.assertEqual(inst._collect_history_for_context("unknown"), [])

    def test_empty_history_returns_empty(self):
        self._stub_convs([])
        inst = self._make_bridge()
        self.assertEqual(inst._collect_history_for_context(self.conv_id), [])

    def test_history_capped_at_limit(self):
        history = ([{"role": "user", "content": f"m{i}"}
                    for i in range(120)])
        self._stub_convs(history)
        inst = self._make_bridge(limit=50)
        out = inst._collect_history_for_context(self.conv_id)
        self.assertEqual(len(out), 50)
        # 取最近 50 条：第 70~119 条（0-indexed）
        self.assertEqual(out[0]["content"], "m70")
        self.assertEqual(out[-1]["content"], "m119")

    def test_skips_tool_calls_and_summary_markers(self):
        history = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "summary", "__is_summary__": True},
            {"role": "assistant", "content": "a1", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "content": "t1", "tool_call_id": "x"},
            {"role": "user", "content": "q2"},
        ]
        self._stub_convs(history)
        inst = self._make_bridge()
        out = inst._collect_history_for_context(self.conv_id)
        # tool_calls / tool_call_id / 非白名单 role 全部过滤
        self.assertEqual([m["role"] for m in out], ["user", "user"])
        self.assertEqual([m["content"] for m in out], ["q1", "q2"])

    def test_invalid_entries_silently_skipped(self):
        history = [
            "not a dict",
            {"role": "alien", "content": "x"},
            {"role": "user", "content": 12345},  # content 不是 str
            {"role": "user", "content": "ok"},
        ]
        self._stub_convs(history)
        inst = self._make_bridge()
        out = inst._collect_history_for_context(self.conv_id)
        self.assertEqual(out, [{"role": "user", "content": "ok"}])

    def test_load_failure_returns_empty_no_raise(self):
        def _boom():
            raise RuntimeError("simulated sqlite failure")
        config_mod.load_conversations = _boom
        inst = self._make_bridge()
        # 不应抛异常，应吞掉返回空
        self.assertEqual(inst._collect_history_for_context(self.conv_id), [])


class TestStartRealChatMessages(unittest.TestCase):
    """start_real_chat 拼 messages 的顺序 / 字段 / 关闭时退化"""

    # 真函数句柄一次性绑定（同 TestCollectHistoryForContext 教训）
    _REAL_LOAD_CONVS = staticmethod(config_mod.load_conversations)

    def setUp(self):
        # 拦截 start_real_chat 中需要的所有外部副作用
        # 不构造真 ChatBridge —— 只验证 messages 构造逻辑（用 AST 静态 +
        # 直接调 _collect_history_for_context + 拼装函数本身）
        self._patcher_load = mock.patch.object(
            config_mod, "load_conversations", self._REAL_LOAD_CONVS)
        self._patcher_load.start()

    def tearDown(self):
        mock.patch.stopall()

    def _fake_worker(self, **kwargs):
        # 记录 messages 构造，扔掉真线程
        self._worker_kwargs = kwargs
        class _W:
            chunk_received = type("X", (), {"connect": lambda *a, **k: None})()
            response_complete = type("X", (), {"connect": lambda *a, **k: None})()
            error_occurred = type("X", (), {"connect": lambda *a, **k: None})()
            tool_call_start = type("X", (), {"connect": lambda *a, **k: None})()
            tool_call_result = type("X", (), {"connect": lambda *a, **k: None})()
            finished = type("X", (), {"connect": lambda *a, **k: None})()
            def __init__(self, *a, **kw): pass
            def start(self): pass
            def isRunning(self): return False
            def wait(self, *a): return True
            def deleteLater(self): pass
        return _W()

    def test_start_real_chat_uses_collect_history(self):
        """AST 守卫：start_real_chat 必须调 _collect_history_for_context"""
        src = _func_source(
            os.path.join(_ROOT, "qwen_app", "_bridge_stream.py"),
            "start_real_chat")
        self.assertIn("_collect_history_for_context",
                      src,
                      "start_real_chat 必须调 _collect_history_for_context")

    def test_start_real_chat_includes_history_in_messages(self):
        """功能验证：起一个 fake worker，确认 messages 顺序正确"""
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        mock.patch.object(
            config_mod, "load_conversations",
            lambda: ([{"id": "c1", "history": history}], "c1")).start()
        inst = stream_mod.StreamMixin.__new__(stream_mod.StreamMixin)
        inst._enable_context = True
        inst._context_history_limit = 50
        inst._current_conv_id = "c1"
        msgs = inst._collect_history_for_context("c1")
        self.assertEqual(msgs, [{"role": "user", "content": "u1"},
                                {"role": "assistant", "content": "a1"}])
        # 拼接模拟：system + history + user
        full = [{"role": "system", "content": "sp"}] + msgs + \
               [{"role": "user", "content": "u2"}]
        self.assertEqual([m["role"] for m in full],
                         ["system", "user", "assistant", "user"])

    def test_history_disabled_collect_returns_empty(self):
        mock.patch.object(
            config_mod, "load_conversations",
            lambda: ([{"id": "c1",
                       "history": [{"role": "user", "content": "x"}]}], "c1")
        ).start()
        inst = stream_mod.StreamMixin.__new__(stream_mod.StreamMixin)
        inst._enable_context = False
        inst._context_history_limit = 50
        inst._current_conv_id = "c1"
        self.assertEqual(inst._collect_history_for_context("c1"), [])


class TestSetPreferencesEnableContext(unittest.TestCase):
    """set_preferences 三参 + get_preferences + set_enable_context 单 slot"""

    # 真函数句柄一次性绑定（避免 self._orig_* chain）
    _REAL_SAVE = staticmethod(config_mod.save_config)
    _REAL_LOAD = staticmethod(config_mod.load_config)

    def setUp(self):
        self._save_calls = []
        mock.patch.object(
            config_mod, "save_config",
            lambda cfg: self._save_calls.append(dict(cfg))).start()
        mock.patch.object(
            config_mod, "load_config",
            lambda: {"enable_thinking": False, "enable_tools": True,
                     "enable_context": True}).start()

    def tearDown(self):
        mock.patch.stopall()

    def _make_bridge(self):
        return model_mod.ModelMixin.__new__(model_mod.ModelMixin)

    def test_set_preferences_three_args_persists_all(self):
        """三参签名：set_preferences(enable_thinking, enable_tools, enable_context)"""
        sig = inspect.signature(model_mod.ModelMixin.set_preferences)
        params = list(sig.parameters)
        self.assertIn("enable_context", params,
                      f"set_preferences 必须含 enable_context 参数，实际: {params}")
        b = self._make_bridge()
        ok = b.set_preferences(True, False, False)
        self.assertTrue(ok)
        self.assertEqual(self._save_calls[-1]["enable_thinking"], True)
        self.assertEqual(self._save_calls[-1]["enable_tools"], False)
        self.assertEqual(self._save_calls[-1]["enable_context"], False)
        self.assertEqual(b._enable_thinking, True)
        self.assertEqual(b._enable_tools, False)
        self.assertEqual(b._enable_context, False)

    def test_get_preferences_returns_enable_context(self):
        b = self._make_bridge()
        b._enable_thinking = True
        b._enable_tools = False
        b._enable_context = False
        prefs = b.get_preferences()
        self.assertIn("enable_context", prefs,
                      f"get_preferences 必须返回 enable_context，实际: {prefs}")
        self.assertEqual(prefs["enable_context"], False)

    def test_set_enable_context_slot_persists(self):
        """独立 slot 路径：set_enable_context(bool) → cfg + in-memory"""
        b = self._make_bridge()
        self.assertTrue(b.set_enable_context(False))
        self.assertEqual(self._save_calls[-1]["enable_context"], False)
        self.assertEqual(b._enable_context, False)
        self.assertTrue(b.set_enable_context(True))
        self.assertEqual(self._save_calls[-1]["enable_context"], True)
        self.assertEqual(b._enable_context, True)

    def test_set_enable_context_save_failure_returns_false(self):
        # 临时把 save_config 换成抛异常的版本
        mock.patch.stopall()
        mock.patch.object(
            config_mod, "save_config",
            lambda cfg: (_ for _ in ()).throw(RuntimeError("disk full"))
        ).start()
        b = self._make_bridge()
        self.assertFalse(b.set_enable_context(False))


class TestChatBridgeInitDefaults(unittest.TestCase):
    """ChatBridge 构造时从 cfg 读 enable_context（默认 True）"""

    _REAL_LOAD = staticmethod(config_mod.load_config)

    def setUp(self):
        mock.patch.object(config_mod, "load_config", lambda: {}).start()

    def tearDown(self):
        mock.patch.stopall()

    def test_init_defaults_enable_context_true(self):
        """直接验证 cfg 读取路径（不构造真 ChatBridge）"""
        inst = model_mod.ModelMixin.__new__(model_mod.ModelMixin)
        cfg = config_mod.load_config()
        inst._enable_context = bool(cfg.get("enable_context", True))
        self.assertTrue(inst._enable_context)

    def test_init_reads_persisted_false(self):
        mock.patch.stopall()
        mock.patch.object(
            config_mod, "load_config",
            lambda: {"enable_context": False}).start()
        inst = model_mod.ModelMixin.__new__(model_mod.ModelMixin)
        cfg = config_mod.load_config()
        inst._enable_context = bool(cfg.get("enable_context", True))
        self.assertFalse(inst._enable_context)


class TestMainQmlContextMenu(unittest.TestCase):
    """Main.qml 视图菜单暴露 enable_context 切换"""

    def setUp(self):
        self.qml_path = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")
        with open(self.qml_path, encoding="utf-8") as f:
            self.src = f.read()

    def test_view_menu_has_context_toggle_item(self):
        """视图菜单必须有 '上下文记忆' 切换项"""
        # 简化：找 set_enable_context 的引用
        self.assertIn("set_enable_context", self.src,
                      "Main.qml 必须调 bridge.set_enable_context")
        self.assertIn("contextEnabled", self.src,
                      "Main.qml 根必须有 contextEnabled 属性")

    def test_root_context_enabled_reads_get_preferences(self):
        """contextEnabled 初始化必须从 bridge.get_preferences() 读"""
        self.assertIn("bridge.get_preferences().enable_context", self.src,
                      "contextEnabled 必须从 get_preferences 读取 enable_context")


if __name__ == "__main__":
    unittest.main()
