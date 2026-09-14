"""Day 20.1 + Day 20.3: QtQuick 路径菜单入口 + chat_bridge 5 slot。

历史：
- Day 20.1: chat_bridge 加 5 个 slot（show_settings_dialog / show_model_manager_dialog
  / show_plugin_manager_dialog / show_automation_manager_dialog / run_automation_check_due），
  Main.qml 加「设置 / 模型 / 自动化」3 组菜单。
- Day 20.3: 用户反馈「菜单栏菜单过多」（8 组），合并到 5 组（文件/编辑/视图/工具/帮助）：
    * 「聊天」并入「工具」
    * 「设置 / 模型 / 自动化」合并为「工具」
  本文件同时覆盖两版结构的关键入口都存在（slot 不变 / 菜单结构以新版为准）。
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
        """编辑 > 偏好设置：调 settings_dialog.show_settings(host)"""
        self.assertTrue(hasattr(self.bridge, "show_settings_dialog"))
        self.assertTrue(callable(self.bridge.show_settings_dialog))

    def test_show_plugin_manager_dialog_slot_exists(self):
        """工具 > 插件管理：调 settings_dialog.show_plugin_manager(host)"""
        self.assertTrue(hasattr(self.bridge, "show_plugin_manager_dialog"))

    def test_show_model_manager_dialog_slot_exists(self):
        """工具 > 模型管理：调 settings_dialog.show_model_manager(host)"""
        self.assertTrue(hasattr(self.bridge, "show_model_manager_dialog"))

    def test_show_automation_manager_dialog_slot_exists(self):
        """工具 > 自动化任务：调 automation_dialogs.show_automation_manager(host)"""
        self.assertTrue(hasattr(self.bridge, "show_automation_manager_dialog"))

    def test_run_automation_check_due_slot_exists(self):
        """工具 > 立即检查：调 host.scheduler.check_due()"""
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
    """Day 20.3: Main.qml 菜单栏必须合并到 5 组：文件/编辑/视图/工具/帮助。
    同时所有 5 个 slot 都必须被引用（防止 Day 20.1 漏补回归）。"""

    def setUp(self):
        self.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                       encoding="utf-8").read()

    def test_five_menus_consolidated(self):
        """5 组菜单都存在"""
        for menu in ["&文件", "&编辑", "&视图", "&工具", "&帮助"]:
            self.assertIn(menu, self.src, f"缺菜单 {menu}")

    def test_no_more_legacy_submenus(self):
        """Day 20.1 拆分的 3 组（设置/模型/自动化）已合并到「工具」，
        不能再有这些顶层 menu（避免用户又看到 8 个菜单）"""
        for legacy in ['title: qsTr("&设置")', 'title: qsTr("模&型")',
                       'title: qsTr("&自动化")']:
            self.assertNotIn(legacy, self.src,
                             f"{legacy} 已合并到「工具」，不应再独立成 menu")

    def test_tools_menu_contains_all_management(self):
        """「工具」菜单要包含：模型管理 / 插件管理 / 自动化任务 / 立即检查
        + 4 项对话操作（重新生成 / 停止 / 朗读 / 删除）"""
        for entry in ["show_model_manager_dialog", "show_plugin_manager_dialog",
                      "show_automation_manager_dialog", "run_automation_check_due",
                      "regenerate_ai_response", "stop_chat", "speak_text", "delete_bubble"]:
            self.assertIn(entry, self.src, f"「工具」菜单缺 {entry} 入口")

    def test_edit_menu_has_preferences(self):
        """「编辑」菜单的「偏好设置...」调 show_settings_dialog（从「设置」合并进来）"""
        self.assertIn("show_settings_dialog", self.src,
                      "「编辑 > 偏好设置」是 show_settings_dialog 的新入口")

    def test_edit_preferences_nearby(self):
        """show_settings_dialog 必须出现在「编辑」menu 的 MenuItem 里（不能仅
        出现在「设置」 menu —— 那就是合并失败）"""
        import re
        # 找编辑 menu：&编辑 → 下一个 &开头 menu 或 } 之间的内容
        m = re.search(r'title:\s*qsTr\("&编辑"\).*?(?=title:\s*qsTr\("&|\Z)',
                      self.src, re.DOTALL)
        self.assertIsNotNone(m, "找不到编辑 menu 段")
        edit_block = m.group(0)
        self.assertIn("show_settings_dialog", edit_block,
                      "show_settings_dialog 必须出现在「编辑」menu 内")


if __name__ == "__main__":
    unittest.main()
