"""Day 20.4 强化：自动化任务管理首次点击不崩溃。

背景：用户反馈「第一次点击自动化任务报错」（log 只有 jieba warnings + 启动日志，
实际 Python traceback 在 stderr 滚动掉了）。

根因：_DialogHost.scheduler property 任何子步（make_openai_client /
discover_plugins / Scheduler.__init__ / QTimer 启动）失败 → 返回 None →
AutomationManagerDialog.__init__ 里 ``parent.scheduler.on_finished`` 立刻
AttributeError。后续 dialog 操作（refresh / _items / add_automation / set_enabled）
也都会炸。

修复：
1. _DialogHost.scheduler 改"逐步 try/except + 退化 _DummyScheduler"模式
   —— 任何子步失败都给一个 automations=[] / 所有方法 no-op 的安全对象
2. AutomationManagerDialog._items() 也加 None / except 兜底
3. on_finished 注册加 try 包

测试（offscreen）：
- 正常路径：构造对话框 + refresh + close 全过（已在 test_day20_2_dialog_host
  覆盖）
- 模拟 lazy init 失败（monkey-patch make_openai_client 抛异常）：
  - 仍然能拿到 scheduler（退化版）
  - 仍然能构造 AutomationManagerDialog
  - refresh() / setRowCount(0) 不抛
  - 关闭不抛
- 极端：Scheduler() 自身构造失败（mock 抛）—— 仍走 _DummyScheduler
"""
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestSchedulerLazyInitBulletproof(unittest.TestCase):
    """任何子步失败都不能让 host.scheduler 返回 None / 抛异常"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app._dialog_host import _DialogHost
        self.bridge = ChatBridge(theme="light")
        self.host = _DialogHost(self.bridge)

    def test_normal_path_returns_scheduler(self):
        """正常路径：host.scheduler 是真 Scheduler"""
        sch = self.host.scheduler
        self.assertIsNotNone(sch)
        self.assertTrue(hasattr(sch, "automations"))
        self.assertTrue(hasattr(sch, "on_finished"))

    def test_make_client_failure_returns_something(self):
        """make_openai_client 抛异常 → 不让 scheduler property 抛 / 不返回 None"""
        from qwen_app import _dialog_host, config as _cfg
        with patch.object(_cfg, "make_openai_client", side_effect=RuntimeError("boom")):
            # 关键：不能再返回 None，否则下游 dialog 全炸
            sch = _dialog_host._DialogHost(self.bridge).scheduler
        self.assertIsNotNone(sch, "make_openai_client 失败后 scheduler 不能为 None")
        # 退化版也必须有 .automations（dialog.refresh 直接读它）
        self.assertTrue(hasattr(sch, "automations"))
        # automations 应该可以是空列表（_DummyScheduler 兜底）
        self.assertIsInstance(sch.automations, list)

    def test_discover_plugins_failure_returns_something(self):
        """discover_plugins 抛异常 → 仍然能拿到 scheduler"""
        from qwen_app import _dialog_host, plugin_manager
        with patch.object(plugin_manager, "discover_plugins", side_effect=RuntimeError("scan fail")):
            sch = _dialog_host._DialogHost(self.bridge).scheduler
        self.assertIsNotNone(sch)
        self.assertIsInstance(sch.automations, list)

    def test_scheduler_ctor_failure_returns_dummy(self):
        """Scheduler() 自身抛异常 → 退化 _DummyScheduler（永不返回 None）"""
        from qwen_app import _dialog_host, scheduler as _sched
        # patch Scheduler.__init__ 抛
        original_init = _sched.Scheduler.__init__
        def boom(self, *a, **kw):
            raise RuntimeError("Scheduler init failed")
        _sched.Scheduler.__init__ = boom
        try:
            from qwen_app.chat_bridge import ChatBridge
            bridge = ChatBridge(theme="light")
            sch = _dialog_host._DialogHost(bridge).scheduler
        finally:
            _sched.Scheduler.__init__ = original_init
        self.assertIsNotNone(sch, "Scheduler() 失败后必须退化，不能 None")
        # DummyScheduler 有 automations=[]（不是真的 Scheduler 实例）
        self.assertIsInstance(sch.automations, list)
        self.assertEqual(sch.automations, [])
        # DummyScheduler 有 on_finished / off_finished / check_due 等所有 dialog 用到的方法
        for method in ["on_finished", "off_finished", "check_due",
                       "set_enabled", "add_automation", "update_automation",
                       "delete_automation", "run_now", "list_runs", "read_log"]:
            self.assertTrue(callable(getattr(sch, method, None)),
                            f"_DummyScheduler 缺 {method}")


class TestAutomationDialogOpensDespiteSchedulerFailures(unittest.TestCase):
    """无论 scheduler 怎么烂，对话框都必须能弹 + refresh + close 都不抛"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")
        from qwen_app._dialog_host import _DialogHost
        self.host = _DialogHost(self.bridge)

    def test_dialog_opens_with_normal_scheduler(self):
        from qwen_app.automation_dialogs import AutomationManagerDialog
        dlg = AutomationManagerDialog(self.host)
        # 弹出来不抛异常
        dlg.deleteLater()

    def test_dialog_opens_with_dummy_scheduler(self):
        """用退化 _DummyScheduler 当 host.scheduler —— 对话框仍能弹 + refresh"""
        from qwen_app._dialog_host import _DummyScheduler
        from qwen_app.automation_dialogs import AutomationManagerDialog
        # 把 host.scheduler 替换成 DummyScheduler
        self.bridge._scheduler = _DummyScheduler()
        dlg = AutomationManagerDialog(self.host)
        # refresh() 不抛
        dlg.refresh()
        # table 是 0 行
        from PyQt5.QtWidgets import QTableWidgetItem
        self.assertEqual(dlg.table.rowCount(), 0)
        dlg.deleteLater()

    def test_dialog_opens_when_make_client_fails(self):
        """monkey-patch make_openai_client 失败 → 弹对话框仍 OK"""
        from qwen_app import config as _cfg
        from qwen_app.automation_dialogs import AutomationManagerDialog
        with patch.object(_cfg, "make_openai_client", side_effect=RuntimeError("boom")):
            # 新 host（共享 bridge 的 _scheduler 缓存）
            from qwen_app._dialog_host import _DialogHost
            host2 = _DialogHost(self.bridge)
            # bridge 还没 _scheduler，第一次访问会触发懒构造
            dlg = AutomationManagerDialog(host2)
            dlg.refresh()  # 不抛
            dlg.deleteLater()


class TestAutomationManagerDialogItemsSafety(unittest.TestCase):
    """_items() 在各种坏状态下都返回 list（绝不抛）"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)

    def test_items_with_normal_host(self):
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app._dialog_host import _DialogHost
        from qwen_app.automation_dialogs import AutomationManagerDialog
        bridge = ChatBridge(theme="light")
        host = _DialogHost(bridge)
        dlg = AutomationManagerDialog(host)
        items = dlg._items()
        self.assertIsInstance(items, list)
        dlg.deleteLater()

    def test_items_with_dummy_scheduler(self):
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app._dialog_host import _DialogHost, _DummyScheduler
        from qwen_app.automation_dialogs import AutomationManagerDialog
        bridge = ChatBridge(theme="light")
        bridge._scheduler = _DummyScheduler()
        host = _DialogHost(bridge)
        dlg = AutomationManagerDialog(host)
        items = dlg._items()
        self.assertIsInstance(items, list)
        self.assertEqual(items, [])
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
