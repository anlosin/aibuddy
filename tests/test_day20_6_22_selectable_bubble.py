# -*- coding: utf-8 -*-
"""Day 20.6.22: 消息气泡可选中复制护栏

根因：MessageBubble.qml 用 Text（QtQuick 2.15 只读控件，selectable 默认 false）
渲染消息正文 + codeText 区，用户无法鼠标拖蓝 / 双击选中 / Ctrl+C 复制一小段内容。
必须改用 TextEdit + readOnly + selectByMouse + selectByKeyboard + persistentSelection。

护栏策略：花括号配对取控件块，避开贪婪正则。
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

BUBBLE_PATH = os.path.join(_ROOT, "qwen_app", "qml", "MessageBubble.qml")


def _strip_qml_comments(src):
    """去除 QML 注释（// 行 + /* */ 块），避免关键字误命中注释里的旧版描述。"""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    out = re.sub(r"//[^\n]*", "", out)
    return out


def _find_block_for_id(src, control_id):
    """取 id=control_id 的控件块（用花括号配对，避开贪婪）。

    QML 的 id 是控件**属性**而非声明头，所以从 id 往前找最近的控件类型名
    （TextEdit / Text / Rectangle 等）+ {，再花括号配对取整块。
    """
    m = re.search(rf"id\s*:\s*{re.escape(control_id)}\b", src)
    if not m:
        return None
    # 向前 250 字符里找最近的控件声明头
    before = src[max(0, m.start() - 250): m.start()]
    head_pat = re.compile(r"(TextEdit|Text|Rectangle|Item|Column|Row|Menu|"
                          r"Dialog|MouseArea)\s*\{")
    h = list(head_pat.finditer(before))
    if not h:
        return None
    start = max(0, m.start() - 250) + h[-1].start()
    depth = 0
    i = start
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
        i += 1
    return None


class TestMessageBubbleSelectable(unittest.TestCase):
    """MessageBubble.qml 静态契约"""

    def setUp(self):
        with open(BUBBLE_PATH, encoding="utf-8") as f:
            self.src = f.read()
        self.code = _strip_qml_comments(self.src)

    def test_body_block_found_and_uses_textedit(self):
        """bodyText 控件必须存在且为 TextEdit"""
        block = _find_block_for_id(self.code, "bodyText")
        self.assertIsNotNone(block, "找不到 bodyText 控件块")
        # 控件头是声明类型（控件块开头就是 TextEdit {）
        head_line = block.lstrip().split("\n", 1)[0].strip()
        self.assertTrue(
            head_line.startswith("TextEdit"),
            f"bodyText 控件头必须是 TextEdit，当前: {head_line!r}")

    def test_body_has_required_selectable_attrs(self):
        """bodyText 必须配齐 selectByMouse / selectByKeyboard / readOnly /
        persistentSelection 四件套"""
        block = _find_block_for_id(self.code, "bodyText")
        self.assertIsNotNone(block)
        for kw, want in (("selectByMouse", "true"),
                         ("selectByKeyboard", "true"),
                         ("readOnly", "true"),
                         ("persistentSelection", "true")):
            m = re.search(rf"\b{kw}\s*:\s*(\w+)", block)
            self.assertIsNotNone(m, f"bodyText 缺属性 {kw}")
            self.assertEqual(m.group(1), want,
                             f"bodyText.{kw} 必须 {want}，当前 {m.group(1)!r}")

    def test_body_text_format_richtext(self):
        """AI/用户消息必须 RichText 渲染（markdown）"""
        block = _find_block_for_id(self.code, "bodyText")
        self.assertRegex(block, r"textFormat:[^\n]*RichText",
                         "AI/用户消息必须 TextEdit.RichText（保留 markdown 渲染）")

    def test_code_block_found_and_uses_textedit(self):
        """codeText 控件必须存在且为 TextEdit"""
        block = _find_block_for_id(self.code, "codeText")
        self.assertIsNotNone(block, "找不到 codeText 控件块")
        head_line = block.lstrip().split("\n", 1)[0].strip()
        self.assertTrue(
            head_line.startswith("TextEdit"),
            f"codeText 控件头必须是 TextEdit，当前: {head_line!r}")

    def test_code_has_required_selectable_attrs(self):
        """codeText 也必须配齐四件套（工具参数 / 结果区同样要可选中）"""
        block = _find_block_for_id(self.code, "codeText")
        self.assertIsNotNone(block)
        for kw, want in (("selectByMouse", "true"),
                         ("selectByKeyboard", "true"),
                         ("readOnly", "true"),
                         ("persistentSelection", "true")):
            m = re.search(rf"\b{kw}\s*:\s*(\w+)", block)
            self.assertIsNotNone(m, f"codeText 缺属性 {kw}")
            self.assertEqual(m.group(1), want,
                             f"codeText.{kw} 必须 {want}，当前 {m.group(1)!r}")

    def test_no_redundant_mousearea_inside_body(self):
        """防回归：之前用 Text 时可能在外面包 MouseArea 试图做选中；
        改 TextEdit 后不应再有冗余 MouseArea 吞掉 TextEdit 自身鼠标事件。"""
        block = _find_block_for_id(self.code, "bodyText")
        self.assertNotIn("MouseArea", block,
                         "bodyText 内不应再叠加 MouseArea，会干扰 TextEdit 选中")
        block_code = _find_block_for_id(self.code, "codeText")
        self.assertNotIn("MouseArea", block_code,
                         "codeText 内不应再叠加 MouseArea，会干扰 TextEdit 选中")

    def test_no_invalid_textedit_property(self):
        """防回归：QtQuick 2.15 的 TextEdit 没有 selectByTouch 等 QQC2 TextEdit 属性，
        误加会让 QML 加载失败（QtQuick.Controls 的 TextEdit 才有）。"""
        for tag in ("bodyText", "codeText"):
            block = _find_block_for_id(self.code, tag)
            self.assertIsNotNone(block, f"找不到 {tag} 控件块")
            for bad in ("selectByTouch", ):
                self.assertNotRegex(block, rf"\b{bad}\s*:",
                                    f"{tag} 不可用 {bad}（QtQuick 2.15 TextEdit 不支持）")

    def test_cursor_visible_disabled(self):
        """cursorVisible: false —— 防止插入符一直闪（只读控件的常见 UX 噪音）"""
        for tag in ("bodyText", "codeText"):
            block = _find_block_for_id(self.code, tag)
            m = re.search(r"cursorVisible\s*:\s*(\w+)", block)
            self.assertIsNotNone(m, f"{tag} 缺 cursorVisible 设置")
            self.assertEqual(m.group(1), "false",
                             f"{tag}.cursorVisible 应 false（防插入符闪烁）")

    def test_copy_menu_still_present(self):
        """三点菜单的「复制」项还在（用户既可整条复制也可框选复制）"""
        self.assertIn("复制", self.code,
                      "三点菜单「复制」项必须保留")
        self.assertIn("copy_to_clipboard", self.code,
                      "复制整条仍走 copy_to_clipboard")


if __name__ == "__main__":
    unittest.main()
