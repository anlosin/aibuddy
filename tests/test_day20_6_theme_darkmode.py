"""Day 20.6 暗黑模式可见性护栏：

1. 主题切换按钮在亮/暗模式下都要有可见底（不能是 transparent，
   否则暗色背景上浅色 emoji 看不见）。
2. MenuBar 在亮/暗模式下背景都不能走系统默认 —— 必须显式绑定
   root.pal.topbarBg，且 MenuBarItem delegate 显式覆盖 background/contentItem。
3. 窗口标题不能有「demo」字样。
4. PyQt5 路径的 QMenu/QMenuBar 在暗色 palette 下用 QSS 强制覆盖。
"""
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_ROOT, "main.py")
_MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")


class TestThemeToggleButton(unittest.TestCase):
    def setUp(self):
        with open(_MAIN_QML, "r", encoding="utf-8") as f:
            self.src = f.read()

    def _toggle_btn_block(self):
        # 抓 themeMa 块：themeMa 出现在「🌙 / ☀️」附近的 Rectangle 里。
        # 用简单字符串切片：从 「root.themeName === "dark" ? "☀️" : "🌙"」之前一个 Rectangle { 开始
        idx = self.src.find('root.themeName === "dark" ? "☀️" : "🌙"')
        self.assertGreater(idx, 0, "找不到切换主题按钮块")
        # 向前找最近的 "Rectangle {" 起始（这一块是按钮的 Rectangle 容器）
        brace_start = self.src.rfind("Rectangle {", 0, idx)
        # 向后找 MouseArea { 之前的 }（结束 Rectangle 容器）
        # MouseArea 一定在该 Rectangle 内；找 200 字符内的 "MouseArea"
        ma = self.src.find("MouseArea", idx)
        # 从 brace_start 起取到 ma 之前
        return self.src[brace_start:ma]

    def test_toggle_button_has_visible_default_color(self):
        block = self._toggle_btn_block()
        self.assertNotIn('"transparent"', block,
                         "切换按钮默认透明背景在暗色模式下不可见")
        # 必须有 inputBg 或 inputBorder 之类的可见色（默认态）
        self.assertRegex(block, r"root\.pal\.(inputBg|inputBorder)\b",
                         "切换按钮默认底色应绑色板（inputBg 或 inputBorder）")

    def test_toggle_button_text_color_adapts(self):
        block = self._toggle_btn_block()
        # emoji 文字颜色要根据 hover/主题自适应（绑定跨行：themeMa.containsMouse ... textPrimary）
        self.assertRegex(block, r"color:[\s\S]+?textPrimary",
                         "切换按钮 emoji 文字色未绑色板（暗色下深字看不见）")


class TestMenuBarThemeBinding(unittest.TestCase):
    def setUp(self):
        with open(_MAIN_QML, "r", encoding="utf-8") as f:
            self.src = f.read()

    def test_menubar_has_palette_background(self):
        # menuBar 必须有 background: Rectangle { color: root.pal.topbarBg ... }
        self.assertRegex(self.src,
                         r"menuBar:\s*MenuBar\s*\{[\s\S]*?background:\s*Rectangle\s*\{[\s\S]*?color:\s*root\.pal\.topbarBg",
                         "MenuBar 缺背景绑定（暗色下系统 Fusion 仍是浅色）")

    def test_menubar_item_delegate_set(self):
        # MenuBarItem delegate 必须显式覆盖 background/contentItem
        self.assertRegex(self.src,
                         r"delegate:\s*MenuBarItem\s*\{[\s\S]*?background:[\s\S]*?contentItem:",
                         "MenuBarItem delegate 没覆盖 background/contentItem")

    def test_all_dropdown_menus_have_background(self):
        # 所有 Menu（Popup）都应该有 background 绑定；否则下拉仍是系统浅色
        # 匹配所有 Menu { title: ... background: ... } 块
        menu_blocks = re.findall(r"Menu\s*\{[^}]*?title:[^}]*?\}", self.src, re.S)
        self.assertGreaterEqual(len(menu_blocks), 5, "MenuBar 应该至少 5 组")
        for blk in menu_blocks:
            self.assertIn("background: Rectangle", blk,
                          "下拉 Menu 缺 background 绑定（暗色下刺眼浅色）")


class TestWindowTitle(unittest.TestCase):
    def setUp(self):
        with open(_MAIN_QML, "r", encoding="utf-8") as f:
            self.src = f.read()

    def test_title_has_no_demo_word(self):
        m = re.search(r'title:\s*"([^"]+)"', self.src)
        self.assertIsNotNone(m)
        title = m.group(1)
        self.assertNotIn("Demo", title, f"窗口标题含 Demo 字样: {title!r}")
        self.assertNotIn("demo", title, f"窗口标题含 demo 字样: {title!r}")


class TestMenuQSSInPyQt5Path(unittest.TestCase):
    def test_qss_covers_menu_role_for_pyside_path(self):
        # PyQt5 路径（chat_window.py）也走 QMenuBar/QMenu；QSS 显式覆盖。
        with open(_MAIN, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("QMenuBar", src, "main.py QSS 缺 QMenuBar")
        self.assertIn("QMenuBar::item:selected", src, "main.py QSS 缺选中态")
        self.assertIn("QMenu", src, "main.py QSS 缺 QMenu")
        self.assertIn("QMenu::item:selected", src, "main.py QSS 缺 Menu 选中态")


if __name__ == "__main__":
    unittest.main()
