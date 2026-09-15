"""Day 20.6 护栏测试：

1. MessageBubble.qml 气泡菜单里每个 `visible:` 条件项都必须配
   `height: visible ? implicitHeight : 0` —— Qt 5.15 Controls 2 的 Menu
   内容项是 QQuickListView 布局，**不会跳过 invisible 的 delegate**，
   隐藏 MenuItem 仍按 implicitHeight 占一行（实测隐藏项占 40px），
   不压 0 就会出现「菜单项之间莫名空行」。

2. QtQuick 启动即拉起自动化调度器：ChatBridge.start_automation_scheduler
   slot 存在，且 main.py 在事件循环就绪后调用它（原来 Scheduler + 30s
   check_due QTimer 只在首次打开「任务管理」对话框时才懒构造，任务永远
   不按时执行）。
"""
import os
import re
import unittest

_QML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "qwen_app", "qml", "MessageBubble.qml")
_MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "main.py")


def _bubble_menu_block():
    """取 MessageBubble.qml 中 bubbleMenu 的 Menu {...} 块（按花括号配对）。"""
    with open(_QML, "r", encoding="utf-8") as f:
        src = f.read()
    start = src.index("Menu {")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("bubbleMenu 块未闭合？")


class TestMenuHiddenItemsCollapseHeight(unittest.TestCase):
    def test_every_visible_item_has_height_collapse(self):
        block = _bubble_menu_block()
        # 按顶层MenuItem/MenuSeparator分段统计：每个含 visible: 绑定的条目
        # 必须同时含 height: visible ? implicitHeight : 0
        items = re.split(r"\n(?=\s{8}(?:MenuItem|MenuSeparator)\s*\{)", block)
        cond_items = [it for it in items if re.search(r"^\s+visible:", it, re.M)]
        self.assertGreater(len(cond_items), 0, "气泡菜单里没找到条件隐藏项（结构变了？）")
        for it in cond_items:
            label = re.search(r'qsTr\("([^"]+)"\)', it)
            self.assertIn("height: visible ? implicitHeight : 0", it,
                          f"条件隐藏项 {label.group(1) if label else '?'} 缺少高度塌陷绑定"
                          f"（Qt 5.15 Menu 的 ListView 不会跳过 invisible 项 → 空行）")

    def test_static_pattern_count_matches(self):
        block = _bubble_menu_block()
        n_visible = len(re.findall(r"^\s+visible:", block, re.M))
        n_collapse = block.count("height: visible ? implicitHeight : 0")
        self.assertEqual(n_visible, n_collapse,
                         f"visible 绑定 {n_visible} 处 vs 高度塌陷 {n_collapse} 处，不匹配")


class TestSchedulerStartsAtBoot(unittest.TestCase):
    def test_bridge_has_start_slot(self):
        from qwen_app.chat_bridge import ChatBridge
        self.assertTrue(hasattr(ChatBridge, "start_automation_scheduler"),
                        "ChatBridge 缺 start_automation_scheduler slot")
        from PyQt5.QtCore import pyqtSlot
        # slot 必须在 MenuMixin 模块里定义（拆分约定：入口方法在 _bridge_menu）
        import qwen_app._bridge_menu as bm
        self.assertTrue(hasattr(bm.MenuMixin, "start_automation_scheduler"))

    def test_main_wires_startup_call(self):
        with open(_MAIN, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_safe_start_scheduler", src)
        self.assertRegex(src, r"QTimer\.singleShot\(0,\s*lambda:\s*_safe_start_scheduler\(bridge\)\)")

    def test_dialog_host_timer_still_wired(self):
        # _DialogHost.scheduler 的 30s QTimer 路径仍在（启动 slot 复用它）
        import qwen_app._dialog_host as dh
        import inspect
        src = inspect.getsource(dh._DialogHost)
        self.assertIn("setInterval(30000)", src)
        self.assertIn("timer.timeout.connect(sch.check_due)", src)


if __name__ == "__main__":
    unittest.main()
