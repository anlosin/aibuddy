"""Day 20.1: QtQuick 路径补「设置 / 模型 / 自动化」菜单入口 + chat_bridge 5 slot。

背景：Day 20 工程师补了 5 组菜单（文件/编辑/视图/聊天/帮助），
但**漏了 PyQt5 chat_window.py 有的 3 组**：
- 设置（模型设置 / 插件管理）
- 模型（多模型快捷切换 + 管理）
- 自动化（任务管理 / 立即检查）

修复：chat_bridge 加 5 个 slot 直接调 PyQt5 对话框（同 QApplication 进程里弹），
Main.qml 加 3 个新 Menu。
"""
import os
import sys
import unittest
import ast

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestChatBridgeMenuSlots(unittest.TestCase):
    """5 个 slot 必须在 chat_bridge 中定义（用户可调用）"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")

    def test_show_settings_dialog_slot_exists(self):
        """设置 > 模型设置：调 settings_dialog.show_settings(bridge)"""
        self.assertTrue(hasattr(self.bridge, "show_settings_dialog"))
        self.assertTrue(callable(self.bridge.show_settings_dialog))
        # 不真弹对话框（offscreen QApplication 在 chat_bridge 模式下弹 dialog 会 crash）
        # 只验证 slot 存在 + 可调用
        # 真 GUI 测试由用户在真窗口验证

    def test_show_plugin_manager_dialog_slot_exists(self):
        self.assertTrue(hasattr(self.bridge, "show_plugin_manager_dialog"))

    def test_show_model_manager_dialog_slot_exists(self):
        self.assertTrue(hasattr(self.bridge, "show_model_manager_dialog"))

    def test_show_automation_manager_dialog_slot_exists(self):
        self.assertTrue(hasattr(self.bridge, "show_automation_manager_dialog"))

    def test_run_automation_check_due_slot_exists(self):
        self.assertTrue(hasattr(self.bridge, "run_automation_check_due"))


class TestChatBridgeSlotNoParams(unittest.TestCase):
    """Day 17 QTBUG-94360 约束：所有 pyqtSignal ≤2 参数；pyqtSlot 无参数约束。
    但为了一致性，新增 slot 都不应带参数。"""

    def setUp(self):
        from qwen_app import chat_bridge
        self.src = open(chat_bridge.__file__, encoding="utf-8").read()

    def test_new_slots_have_no_params(self):
        """5 个新 slot 应无参数（@pyqtSlot() 无内容）"""
        import re
        for name in ["show_settings_dialog", "show_plugin_manager_dialog",
                     "show_model_manager_dialog", "show_automation_manager_dialog",
                     "run_automation_check_due"]:
            pattern = rf"@pyqtSlot\(\)\s*\n\s*def\s+{name}\("
            self.assertRegex(self.src, pattern,
                              f"{name} 必须用 @pyqtSlot() 无参形式")


class TestMainQmlMenuStructure(unittest.TestCase):
    """Main.qml 菜单栏必须含「设置 / 模型 / 自动化」3 组"""

    def setUp(self):
        self.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                       encoding="utf-8").read()

    def test_settings_menu_exists(self):
        """设置菜单含 模型设置 / 插件管理"""
        self.assertIn("&设置", self.src, "缺设置菜单")
        self.assertIn("show_settings_dialog", self.src, "缺 show_settings 入口")
        self.assertIn("show_plugin_manager_dialog", self.src, "缺 show_plugin_manager 入口")

    def test_model_menu_exists(self):
        self.assertIn("模&型", self.src, "缺模型菜单")
        self.assertIn("show_model_manager_dialog", self.src, "缺 show_model_manager 入口")

    def test_automation_menu_exists(self):
        self.assertIn("&自动化", self.src, "缺自动化菜单")
        self.assertIn("show_automation_manager_dialog", self.src, "缺自动化任务管理入口")
        self.assertIn("run_automation_check_due", self.src, "缺立即检查入口")


if __name__ == "__main__":
    unittest.main()
