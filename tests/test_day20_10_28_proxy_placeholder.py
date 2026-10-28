# -*- coding: utf-8 -*-
"""Day 20.10.28: 模型编辑表单「代理:」placeholder 护栏

根因：模型编辑表单里「代理:」输入框的 placeholder 只写死了 http 协议
（"http://127.0.0.1:7890（留空=直连）"），会让用户误以为 socks5 代理不被支持。
实际 config.make_openai_client 直接把 proxy 字符串交给 httpx.Client(proxy=...)，
httpx 0.28 + socksio 已支持 socks5:// 与 socks5h://（socks5h 更快，域名交由
代理解析）。

护栏策略：用 ast 静态扫描 settings_dialog.py，定位 le_proxy.setPlaceholderText(...)
调用的字面量参数并断言文案语义 —— 不真起 QWidget（避免 GUI 依赖 / offscreen
环境差异），也不会因函数重构到别的模块而误伤。

注意：本护栏只约束「代理」输入框的 placeholder，不约束其它输入框文案。
"""
import ast
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SETTINGS_DIALOG = os.path.join(_ROOT, "qwen_app", "settings_dialog.py")

# 「代理」输入框的控件变量名（当前为 le_proxy）
_PROXY_WIDGET = "le_proxy"


def _load_module_ast():
    """解析 settings_dialog.py 为 AST。"""
    with open(SETTINGS_DIALOG, encoding="utf-8") as f:
        src = f.read()
    return ast.parse(src, filename=SETTINGS_DIALOG), src


def _find_proxy_placeholder():
    """定位 le_proxy.setPlaceholderText(<字面量>) 并返回该字面量字符串。

    找不到时返回 None（由调用方断言报错）。
    隐式字符串拼接（"a" "b"）在 AST 里是单个 Constant，无需特殊处理。
    """
    tree, _src = _load_module_ast()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "setPlaceholderText":
            continue
        # 必须是 <proxy_widget>.setPlaceholderText(...)
        target = func.value
        if not isinstance(target, ast.Name) or target.id != _PROXY_WIDGET:
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


class TestProxyPlaceholder(unittest.TestCase):
    """模型编辑表单「代理:」placeholder 静态契约"""

    def setUp(self):
        self.placeholder = _find_proxy_placeholder()

    # ── 1. 必须明确提到 socks5 ────────────────────────────────
    def test_placeholder_mentions_socks5(self):
        """placeholder 必须提到 socks5（socks5:// 或 socks5h://）。

        修复前只写 http://，用户会以为 socks5 代理不支持。
        """
        self.assertIsNotNone(
            self.placeholder,
            f"settings_dialog.py 里找不到 {_PROXY_WIDGET}.setPlaceholderText(...)")
        self.assertIn(
            "socks5", self.placeholder,
            f"代理 placeholder 必须提到 socks5，当前文案: {self.placeholder!r}")

    # ── 2. 必须保留 http 示例 ────────────────────────────────
    def test_placeholder_mentions_http_fallback(self):
        """placeholder 必须仍含 http:// 示例 —— 不能只写 socks5 把 http 挤掉。

        socks5 需要额外的 socksio 依赖，http 代理仍是零依赖的兜底路径。
        """
        self.assertIsNotNone(self.placeholder)
        self.assertIn(
            "http://", self.placeholder,
            f"代理 placeholder 必须保留 http:// 示例，当前文案: {self.placeholder!r}")

    # ── 3. 必须保留「直连」语义 ──────────────────────────────
    def test_placeholder_mentions_direct_option(self):
        """placeholder 必须保留「留空=直连」语义。

        proxy 留空表示不走代理（config.make_openai_client 不注入 http_client），
        这是最常用的配置，提示不能丢。
        """
        self.assertIsNotNone(self.placeholder)
        self.assertIn(
            "直连", self.placeholder,
            f"代理 placeholder 必须保留「留空=直连」提示，当前文案: {self.placeholder!r}")

    # ── 4. 优先推荐 socks5h://（域名交由代理解析，更快）────────
    def test_placeholder_mentions_socks5h_preferred(self):
        """placeholder 应包含 socks5h:// —— socks5h 把域名解析交给代理解析，
        实测明显快于 socks5:// 与 http://。socks5:// 仍然可用（httpx 接受），
        因此本断言只要求「提到 socks5h」，不排斥 socks5://。
        """
        self.assertIsNotNone(self.placeholder)
        self.assertIn(
            "socks5h://", self.placeholder,
            f"代理 placeholder 应优先提示 socks5h://，当前文案: {self.placeholder!r}")

    # ── 5. 防回归：不得把「留空」语义改写掉 ────────────────────
    def test_placeholder_keeps_blank_means_direct_phrase(self):
        """「留空」二字必须保留（与「直连」互为表里）。"""
        self.assertIsNotNone(self.placeholder)
        self.assertIn(
            "留空", self.placeholder,
            f"代理 placeholder 必须保留「留空」提示，当前文案: {self.placeholder!r}")


if __name__ == "__main__":
    unittest.main()
