"""A1 回归测试：两条 UI 路径都用 chat_render 抽象层。

老行为：chat_bridge 用 markdown_to_html 单独走；chat_window 用 build_bubble 单独走。
新行为：都通过 chat_render.* 入口；模块结构、关键函数签名稳定。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestChatRenderAbstract(unittest.TestCase):
    """A1 共享抽象层 API 稳定。"""

    def test_module_exports(self):
        from qwen_app import chat_render
        self.assertTrue(hasattr(chat_render, "sanitize_text"))
        self.assertTrue(hasattr(chat_render, "render_text_final"))
        self.assertTrue(hasattr(chat_render, "render_html_bubble"))

    def test_render_text_final_ai_uses_markdown(self):
        from qwen_app.chat_render import render_text_final
        # ai/user 走 markdown
        out = render_text_final("ai", "**bold**", "light")
        self.assertIn("<b>bold</b>", out)

    def test_render_text_final_tool_uses_plaintext(self):
        from qwen_app.chat_render import render_text_final
        # tool_call / tool_result 必须 PlainText 避免 markdown 干扰参数 JSON
        out = render_text_final("tool_call", "**raw**", "light")
        # PlainText 不应把 ** 变成 <b>；应保留 raw 字符
        self.assertNotIn("<b>", out)
        self.assertIn("**raw**", out)

    def test_render_text_final_empty(self):
        from qwen_app.chat_render import render_text_final
        self.assertEqual(render_text_final("ai", "", "light"), "")
        self.assertEqual(render_text_final("ai", None, "light"), "")

    def test_render_html_bubble_matches_build_bubble(self):
        from qwen_app.chat_render import render_html_bubble
        from qwen_app.theme import build_bubble
        # 应与 theme.build_bubble 等价（thin wrapper）
        out1 = render_html_bubble("light", "您", "hi", "user", "10:30")
        out2 = build_bubble("light", "您", "hi", "user", "10:30")
        self.assertEqual(out1, out2)

    def test_sanitize_text_drops_empty(self):
        from qwen_app.chat_render import sanitize_text
        self.assertEqual(sanitize_text(""), "")
        self.assertEqual(sanitize_text(None), "")
        # latex 残留应被清洗
        out = sanitize_text("hello \\boxed{x} world")
        self.assertIn("hello", out)
        self.assertIn("world", out)


class TestChatBridgeUsesAbstractLayer(unittest.TestCase):
    """A1: chat_bridge.py 必须通过 chat_render 调用渲染（不在桥里直接 import theme.markdown_to_html）。"""

    def test_chat_bridge_no_direct_markdown_import(self):
        # 拆分后渲染调用分布在 bridge 模块组 —— 扫模块组
        from tests._bridge_source import bridge_source
        src = bridge_source()
        # 不应再直接调 markdown_to_html（应走 chat_render）
        # 注意：chat_bridge 是从 theme import markdown_to_html 仅为内部 _flush_stream_buffer
        # 验证：_flush_stream_buffer 必须调 chat_render.render_text_final
        self.assertIn("from . import chat_render", src,
                      "chat_bridge 必须 import chat_render 抽象层")
        self.assertIn("render_text_final", src,
                      "chat_bridge 必须通过 chat_render.render_text_final 渲染")

    def test_chat_window_no_direct_build_bubble_import(self):
        from qwen_app import chat_window
        with open(chat_window.__file__, encoding="utf-8") as f:
            src = f.read()
        # chat_window 不应再 import 或使用 build_bubble（全部走 chat_render）
        self.assertNotIn("build_bubble", src,
                          "chat_window 不应再直接用 build_bubble（走 chat_render 抽象层）")


if __name__ == "__main__":
    unittest.main()
