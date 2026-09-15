# -*- coding: utf-8 -*-
"""Day 20.6.4 护栏：Menu 自定义 background 会把弹出宽度拖成 0。

真因（真平台探针 tests/manual_menubar_open_probe.py 实测）：
Day 20.6 给菜单栏 5 个子 Menu 换了自定义 background: Rectangle
（implicitWidth=0），Popup 隐式宽度 = max(background.implicitWidth,
contentItem.implicitWidth) 也随之变 0（高度正常，ListView 的
implicitHeight=contentHeight）→ opened=True 但 w=0 → 弹出窗口零宽度，
用户感知为「点菜单没反应、菜单弹不出来」。

修复：每个 Menu 显式 implicitWidth: root.menuImplicitWidth(<id>)
（按最宽 MenuItem 的 implicitWidth + 20 计算）。
"""
import os
import re
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QML = os.path.join(PROJ, 'qwen_app', 'qml')


def _strip_comments(src):
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("//"))


class TestMenuWidth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(QML, 'Main.qml'), encoding='utf-8') as f:
            cls.src = _strip_comments(f.read())

    def test_width_function_exists(self):
        self.assertIsNotNone(
            re.search(r'function\s+menuImplicitWidth\s*\(\s*m\s*\)', self.src),
            'Main.qml 必须有 menuImplicitWidth(m) 宽度计算函数')

    def test_function_uses_item_implicit_width(self):
        m = re.search(r'function\s+menuImplicitWidth[\s\S]*?\n    \}', self.src)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn('itemAt', body)
        self.assertIn('implicitWidth', body)
        # 必须取 max（容纳最宽菜单项），不能用固定常量
        self.assertIn('Math.max', body)

    def test_all_five_menus_bind_implicit_width(self):
        for name in ('fileMenu', 'editMenu', 'viewMenu', 'toolsMenu', 'helpMenu'):
            self.assertRegex(
                self.src,
                r'id:\s*%s\s*\n\s*implicitWidth:\s*root\.menuImplicitWidth\(%s\)'
                % (name, name),
                f'{name} 必须显式绑定 implicitWidth（自定义 background 会把'
                f'隐式宽度拖成 0 → 弹出窗口零宽度，菜单「弹不出来」）')

    def test_menus_keep_custom_background(self):
        # Day 20.6 的暗色诉求不能丢：5 个子 Menu 仍要有 background 绑色板
        for name in ('fileMenu', 'editMenu', 'viewMenu', 'toolsMenu', 'helpMenu'):
            m = re.search(r'id:\s*%s[\s\S]{0,220}?background:\s*Rectangle' % name,
                          self.src)
            self.assertIsNotNone(m, f'{name} 的暗色 background 丢失')


if __name__ == '__main__':
    unittest.main()
