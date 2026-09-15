"""Day 20.6.3：菜单栏暗色可读性 —— 窗口级 palette 绑主题色板。

根因（offscreen 探针实测）：QQC2 控件默认文字色来自系统 palette（近黑
#26282A），黑字配暗色 topbarBg / 菜单背景 → 菜单栏文字不可见、弹出菜单
内容不可读（用户感知为「点不了 / 没反应」）。

修复：ApplicationWindow 上绑定 palette.text / palette.windowText 等 →
MenuBarItem / MenuItem 的默认 IconLabel 继承（实测 #26282A → #E8E8E8），
切主题自动跟随。同时禁止在 MenuBarItem delegate 上覆盖 palette 角色
（Day 20.6.1 踩坑：破坏 hit test，菜单栏点不动）。
"""
import os, re, unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")


class TestMenuBarPalette(unittest.TestCase):
    def setUp(self):
        with open(MAIN_QML, "r", encoding="utf-8") as f:
            self.src = f.read()

    def test_root_window_binds_palette_text_roles(self):
        """ApplicationWindow 必须绑定 palette.text / palette.windowText 到主题色板，
        否则暗色下菜单文字是系统调色板的近黑色（黑字暗底不可见）。"""
        for role in ["palette.text", "palette.windowText", "palette.buttonText",
                     "palette.highlight"]:
            self.assertRegex(
                self.src,
                re.escape(role) + r"\s*:\s*root\.pal\.",
                f"ApplicationWindow 缺少 {role} 绑定（暗色下菜单文字不可见）")

    def test_menubaritem_delegate_has_no_palette_override(self):
        """MenuBarItem delegate 内禁止 palette.* 角色覆盖 —— Day 20.6.1 踩坑：
        palette.text/highlight/highlightedText 覆盖破坏 MenuBarItem 的
        hover/press 状态机，整个菜单栏点不动。改色只允许走窗口级 palette。"""
        m = re.search(r"delegate:\s*MenuBarItem\s*\{", self.src)
        self.assertIsNotNone(m, "找不到 MenuBarItem delegate")
        # 从 delegate 开始截取一个平衡括号块
        start = m.start()
        depth, i = 0, start
        while i < len(self.src):
            if self.src[i] == "{":
                depth += 1
            elif self.src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = self.src[start:i + 1]
        block_no_comments = re.sub(r"//[^\n]*", "", block)
        self.assertNotRegex(block_no_comments, r"\bpalette\.\w+\s*:",
                            "MenuBarItem delegate 上覆盖 palette 角色会破坏点击状态机")

    def test_menubaritem_delegate_keeps_background_only(self):
        """delegate 只覆盖 background（绑色板），contentItem/palette 走默认。"""
        m = re.search(r"delegate:\s*MenuBarItem\s*\{", self.src)
        self.assertIsNotNone(m)
        start = m.start()
        depth, i = 0, start
        while i < len(self.src):
            if self.src[i] == "{":
                depth += 1
            elif self.src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = re.sub(r"//[^\n]*", "", self.src[start:i + 1])
        self.assertIn("background: Rectangle", block,
                      "MenuBarItem delegate 应只覆盖 background 绑色板")
        self.assertNotIn("contentItem:", block,
                         "MenuBarItem delegate 不应覆盖 contentItem（会破坏助记符）")


if __name__ == "__main__":
    unittest.main()
