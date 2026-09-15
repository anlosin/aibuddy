"""Day 20.6.1：菜单栏字符前不应有字面 '&'（Qt 助记符渲染）。"""
import os, re, unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")


class TestMenuBarMnemonic(unittest.TestCase):
    def setUp(self):
        with open(MAIN_QML, "r", encoding="utf-8") as f:
            self.src = f.read()

    def test_menubaritem_keeps_default_label_content_item(self):
        """MenuBarItem delegate 必须保留默认 Label contentItem（处理 '&' 助记符）。

        上一版 override 成 Text，导致 '&文件' 渲染为 '&文件'（多了一个 & 符号），
        因为只有 Label/Button 等带 text 属性的 QQC2 控件会自动处理 '&' 作为
        助记符（Alt+后一位高亮），Text 控件把它当字面字符。

        注释里也允许出现「contentItem: Text」字样（Day 20.6.2 把教训写进去了），
        只检查实际 QML 属性赋值。
        """
        m = re.search(
            r"delegate:\s*MenuBarItem\s*\{[^/]*?(?=\n\s*//|\n\s*Menu\s|\n\s*\})",
            self.src, re.DOTALL)
        self.assertIsNotNone(m, "找不到 MenuBarItem delegate 块")
        block = m.group(0)
        # 去掉注释行后再断言
        block_no_comments = re.sub(r"//[^\n]*", "", block)
        self.assertNotIn("contentItem: Text", block_no_comments,
                         "MenuBarItem 用 Text 作 contentItem 会字面显示 '&'")

    def test_menu_titles_keep_ampersand_mnemonic(self):
        """5 个顶层 Menu title 应该用 & 前缀 —— 助记符，由 Label 渲染时去掉。"""
        for title in ["&文件", "&编辑", "&视图", "&工具", "&帮助"]:
            self.assertIn(f'title: qsTr("{title}")', self.src,
                          f"MenuBar 缺菜单 {title}（助记符）")


if __name__ == "__main__":
    unittest.main()
