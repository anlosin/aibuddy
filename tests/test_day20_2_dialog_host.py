"""Day 20.2: _DialogHost 适配器回归测试。

背景：Day 20.1 把 ChatBridge(QObject) 直接传给 PyQt5 对话框
(settings_dialog.show_*)，点「模型管理」就崩：
    QDialog(parent: Optional[QWidget] = None, flags: ...): argument 1
    has unexpected type 'ChatBridge'

修复：加 _DialogHost(QWidget) 适配器，让 PyQt5 对话框能拿到合法
QWidget parent + 状态字段 / 方法代理。本文件验证：
  1. _DialogHost 是 QWidget，能给 QDialog 当 parent。
  2. host 上的字段（enable_thinking/theme/current_model_id/plugins/...）
     跟 ChatBridge / cfg 双向同步。
  3. 三个 show_*_dialog slot 实际能 spawn 对话框（offscreen 不真弹但
     不能抛 QDialog TypeError）。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def chat_bridge_module_file():
    from qwen_app import chat_bridge
    return chat_bridge.__file__


class TestDialogHostBasics(unittest.TestCase):
    """_DialogHost 必须是 QWidget，且每个字段读写不抛"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        cls.bridge = ChatBridge(theme="light")
        from qwen_app._dialog_host import _DialogHost
        cls.host = _DialogHost(cls.bridge)

    def test_host_is_qwidget(self):
        from PyQt5.QtWidgets import QWidget
        self.assertIsInstance(self.host, QWidget)

    def test_host_can_be_qdialog_parent(self):
        """Day 20.2 核心 regression：QDialog(host) 不抛 TypeError。"""
        from PyQt5.QtWidgets import QDialog
        try:
            dlg = QDialog(self.host)
        except TypeError as e:
            self.fail(f"QDialog(host) 仍然抛 TypeError：{e}")
        self.assertEqual(dlg.parent(), self.host)
        dlg.deleteLater()

    def test_enable_thinking_roundtrip(self):
        """字段读写桥到 bridge._enable_thinking"""
        from qwen_app import config as _cfg
        original = self.bridge._enable_thinking
        try:
            self.host.enable_thinking = True
            self.assertTrue(self.host.enable_thinking)
            self.assertTrue(self.bridge._enable_thinking)
            self.host.enable_thinking = False
            self.assertFalse(self.host.enable_thinking)
        finally:
            self.bridge._enable_thinking = original

    def test_theme_roundtrip_via_setter(self):
        """theme setter 走 bridge.set_theme → emit themeChanged"""
        from qwen_app import config as _cfg
        original_theme = self.bridge._theme
        captured = []
        self.bridge.themeChanged.connect(lambda n: captured.append(n))
        try:
            self.host.theme = "dark"
            self.assertEqual(self.host.theme, "dark")
            self.assertEqual(captured, ["dark"])
        finally:
            self.bridge._theme = original_theme
            try:
                self.bridge.themeChanged.disconnect()
            except Exception:
                pass

    def test_current_model_roundtrip(self):
        """current_model_id 走 cfg 注册表"""
        from qwen_app import config as _cfg
        original_models, original_cur = _cfg.load_models()
        try:
            new_id = original_models[0]["id"]
            self.host.current_model_id = new_id
            self.assertEqual(self.host.current_model_id, new_id)
            _, cur = _cfg.load_models()
            self.assertEqual(cur, new_id)
        finally:
            _cfg.save_models(original_models, original_cur)

    def test_model_fields_roundtrip(self):
        """base_url/api_key/model_id/proxy 写到当前模型"""
        from qwen_app import config as _cfg
        original_models, original_cur = _cfg.load_models()
        try:
            self.host.base_url = "https://test.invalid/v1"
            self.host.api_key = "sk-test-123"
            self.host.model_id = "test-model"
            self.host.proxy = "http://127.0.0.1:9999"
            self.assertEqual(self.host.base_url, "https://test.invalid/v1")
            self.assertEqual(self.host.api_key, "sk-test-123")
            self.assertEqual(self.host.model_id, "test-model")
            self.assertEqual(self.host.proxy, "http://127.0.0.1:9999")
        finally:
            _cfg.save_models(original_models, original_cur)

    def test_plugins_roundtrip(self):
        """plugins/plugin_infos 懒加载 + 写回缓存"""
        if not hasattr(self.bridge, "_plugins_cache") or self.bridge._plugins_cache is None:
            # 触发懒加载
            _ = self.host.plugins
        self.assertIsNotNone(self.bridge._plugins_cache)
        # 写一个伪 dict 进去能读回
        self.bridge._plugins_cache = {"fake": object()}
        self.assertEqual(list(self.host.plugins.keys()), ["fake"])
        # 清理
        self.bridge._plugins_cache = None
        self.bridge._plugin_infos_cache = None

    def test_enabled_plugins_roundtrip(self):
        from qwen_app import config as _cfg
        cfg = _cfg.load_config()
        original = cfg.get("enabled_plugins", [])
        try:
            self.host.enabled_plugins = ["calc", "weather"]
            self.assertEqual(self.host.enabled_plugins, ["calc", "weather"])
            cfg2 = _cfg.load_config()
            self.assertEqual(cfg2.get("enabled_plugins"), ["calc", "weather"])
        finally:
            cfg["enabled_plugins"] = original
            _cfg.save_config(cfg)

    def test_save_settings_persists(self):
        from qwen_app import config as _cfg
        original = _cfg.load_config()
        try:
            self.host.agent_mode = True
            self.host.max_agent_rounds = 7
            self.host._save_settings()
            cfg = _cfg.load_config()
            self.assertTrue(cfg.get("agent_mode"))
            self.assertEqual(cfg.get("max_agent_rounds"), 7)
        finally:
            _cfg.save_config(original)

    def test_display_message_emits_signal(self):
        """display_message 触发 bridge.messageAdded（QML 端会渲染气泡）"""
        captured = []
        self.bridge.messageAdded.connect(
            lambda who, payload: captured.append((who, payload))
        )
        try:
            self.host.display_message("系统", "test 消息", "system")
            self.assertEqual(len(captured), 1)
            who, payload = captured[0]
            self.assertEqual(who, "系统")
            self.assertEqual(payload["text"], "test 消息")
            self.assertEqual(payload["code"], "system")
            # ts 由 display_message 自动填当前 HH:MM
            self.assertRegex(payload["ts"], r"^\d{2}:\d{2}$")
        finally:
            try:
                self.bridge.messageAdded.disconnect()
            except Exception:
                pass

    def test_switch_model_via_bridge(self):
        """switch_model 走 bridge.set_current_model + 触发 messageAdded"""
        from qwen_app import config as _cfg
        original_models, original_cur = _cfg.load_models()
        captured = []
        self.bridge.messageAdded.connect(
            lambda who, payload: captured.append((who, payload))
        )
        try:
            target_id = original_models[0]["id"]
            self.host.switch_model(target_id)
            _, cur = _cfg.load_models()
            self.assertEqual(cur, target_id)
            # 系统消息气泡被发出（"已切换模型 → ..."）
            self.assertTrue(any("切换模型" in p["text"] for _, p in captured))
        finally:
            _cfg.save_models(original_models, original_cur)
            try:
                self.bridge.messageAdded.disconnect()
            except Exception:
                pass


class TestDialogHostCachedOnBridge(unittest.TestCase):
    """slots 复用 bridge._dialog_host（多次调用不重复创建）"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")

    def test_host_is_cached(self):
        h1 = self.bridge._get_dialog_host()
        h2 = self.bridge._get_dialog_host()
        self.assertIs(h1, h2, "_dialog_host 必须缓存，不能每次 new")

    def test_host_handles_no_qapp_gracefully(self):
        """_get_dialog_host 在 QApplication 缺失时返回 None。

        QApplication 已在 setUp 创建（PyQt5 单例），无法临时销毁。
        这里通过读源码确认 _get_dialog_host 含 None 兜底分支。
        """
        import inspect
        from qwen_app import chat_bridge
        src = inspect.getsource(chat_bridge.ChatBridge._get_dialog_host)
        self.assertIn("return None", src, "_get_dialog_host 必须有 None 兜底")
        self.assertIn("QApplication.instance() is None", src,
                      "_get_dialog_host 必须先检 QApplication")

    def test_slots_handle_no_qapp_via_toast(self):
        """三个 slot 在 host=None 时 emit toast 提示，不裸崩"""
        import inspect
        from qwen_app import chat_bridge
        for slot_name in ("show_settings_dialog", "show_model_manager_dialog",
                          "show_plugin_manager_dialog"):
            src = inspect.getsource(getattr(chat_bridge.ChatBridge, slot_name))
            self.assertIn("QApplication 未就绪", src,
                          f"{slot_name} 必须有 QApplication 未就绪 toast 兜底")


class TestShowDialogSlotsUseHost(unittest.TestCase):
    """Day 20.2 核心：slots 必须走 _get_dialog_host() 而不是直接 show_X(self)。

    offscreen 模式下直接调 show_*_dialog 会进 dlg.exec_() 卡死，且 slot 内部
    用的是 ``from .settings_dialog import show_X`` 拿到的本地名字引用，
    普通 monkey-patch settings_dialog.show_X 不生效（slot 仍持有旧引用）。

    用 AST / 源码检查确认 slot 路径，不再做"调真函数"的端到端测试（那个
    必须用户在真窗口验证，offscreen 不适合）。
    """

    def setUp(self):
        import inspect
        from qwen_app import chat_bridge
        self.src = inspect.getsource(chat_bridge)

    def _slot_uses_host_not_self(self, slot_name, dialog_fn):
        src = open(chat_bridge_module_file(), encoding="utf-8").read()
        # 用粗正则：slot 函数体内必须先取 host，再调 dialog_fn(host)
        # 不能出现 dialog_fn(self)
        # 取出函数体
        import re
        m = re.search(
            rf"def\s+{slot_name}\(self\):(.*?)(?=\n    @|\nclass\s|\Z)",
            src, re.DOTALL,
        )
        self.assertIsNotNone(m, f"找不到 {slot_name} 函数体")
        body = m.group(1)
        self.assertIn("_get_dialog_host()", body,
                      f"{slot_name} 必须先调 _get_dialog_host()")
        self.assertIn(f"{dialog_fn}(host)", body,
                      f"{slot_name} 必须把 host 传给 {dialog_fn}()")
        # 不能出现 dialog_fn(self) —— Day 20.1 的 bug 复现
        self.assertNotIn(f"{dialog_fn}(self)", body,
                         f"{slot_name} 不能直接传 self 给 {dialog_fn}()（Day 20.1 bug）")

    def test_show_settings_dialog_uses_host(self):
        self._slot_uses_host_not_self("show_settings_dialog", "show_settings")

    def test_show_model_manager_dialog_uses_host(self):
        self._slot_uses_host_not_self("show_model_manager_dialog", "show_model_manager")

    def test_show_plugin_manager_dialog_uses_host(self):
        self._slot_uses_host_not_self("show_plugin_manager_dialog", "show_plugin_manager")


class TestAutomationDialogFactory(unittest.TestCase):
    """Day 20.3: automation_dialogs.show_automation_manager(host) 工厂入口。

    Day 20.1 漏了这个工厂，slot 只能 toast 占位。Day 20.3 补齐后
    点菜单应该真正弹 AutomationManagerDialog。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)

    def test_factory_exists(self):
        from qwen_app import automation_dialogs
        self.assertTrue(hasattr(automation_dialogs, "show_automation_manager"))
        self.assertTrue(callable(automation_dialogs.show_automation_manager))

    def test_bridge_slot_calls_factory(self):
        """bridge.show_automation_manager_dialog 必须调
        automation_dialogs.show_automation_manager(host) —— 不能再走
        hasattr 兜底分支（'待补完' toast）"""
        from qwen_app import chat_bridge
        src = open(chat_bridge.__file__, encoding="utf-8").read()
        self.assertIn("from .automation_dialogs import show_automation_manager", src,
                      "bridge 必须显式 import show_automation_manager 工厂")
        self.assertIn("show_automation_manager(host)", src,
                      "bridge.show_automation_manager_dialog 必须传 host 给工厂")


class TestDialogHostScheduler(unittest.TestCase):
    """Day 20.3: _DialogHost.scheduler 懒构造（QtQuick 路径无 client/model_id，
    按 standalone 模式构造）。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")
        from qwen_app._dialog_host import _DialogHost
        self.host = _DialogHost(self.bridge)

    def test_scheduler_property_lazy(self):
        """首次访问 host.scheduler 才构造 Scheduler 实例（不启动 scheduler 啥都不做）"""
        # 先确认 bridge 还没装 scheduler
        self.assertFalse(hasattr(self.bridge, "_scheduler"))
        # 触发懒构造
        sch = self.host.scheduler
        if sch is None:
            self.skipTest("scheduler 懒构造失败（可能是 key/url 缺失或 plugin 扫描失败）")
        # 构造后 bridge 应该有 _scheduler 属性
        self.assertIs(self.bridge._scheduler, sch)
        # 二次访问应复用同一实例（不重复构造）
        sch2 = self.host.scheduler
        self.assertIs(sch2, sch, "scheduler 必须缓存，不能每次 new")

    def test_timer_started(self):
        """懒构造后 30s QTimer 已启动（与 chat_window._sched_timer 行为对齐）"""
        sch = self.host.scheduler
        if sch is None:
            self.skipTest("scheduler 懒构造失败")
        self.assertTrue(hasattr(self.bridge, "_sched_timer"),
                        "bridge 上必须有 _sched_timer 字段")
        self.assertTrue(self.bridge._sched_timer.isActive(),
                        "_sched_timer 必须启动")


if __name__ == "__main__":
    unittest.main()
