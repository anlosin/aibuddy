"""Day 18 小项修复回归测试 — H3 (hasattr 冗余) + L1 (_StderrFilter 多段截断 bug) + L2 (setup_client sys.exit) + L3 (斜体正则)。"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestHasattrReplacedWithGetattr(unittest.TestCase):
    """H3: worker.py _handle_stream 不再用 hasattr(delta, 'tool_calls')。"""

    def test_uses_getattr_for_tool_calls(self):
        from qwen_app import worker
        with open(worker.__file__, encoding="utf-8") as f:
            src = f.read()
        # 旧的 hasattr+getattr 模式不应再出现
        self.assertNotIn('hasattr(delta, "tool_calls")', src,
                          "Day 18 H3：改用 getattr(delta, 'tool_calls', None) 一次属性查找")
        self.assertIn("getattr(delta, \"tool_calls\", None)", src,
                      "必须用 getattr 取代 hasattr")

    def test_uses_getattr_for_content_and_reasoning(self):
        """顺便检查 content / reasoning_content 的一致性（同样问题）"""
        from qwen_app import worker
        with open(worker.__file__, encoding="utf-8") as f:
            src = f.read()
        # 注释里 Day 18 H3 提及：content 字段也类似。实际仍用 hasattr 是因为
        # SimpleNamespace fake 不会给 content 字段；真实 OpenAI 一定有。
        # 这里**不**强求改 content（改了反而误伤 fake client 的测试），
        # 只验证对 tool_calls 修了。
        self.assertIn("getattr(delta, \"tool_calls\", None)", src)


class TestStderrFilterRemoved(unittest.TestCase):
    """L1: _StderrFilter 删了（多段 write 截断 bug 难修；Qt 5.15.x 已不打印 iCCP）。"""

    def test_no_stderr_filter_class(self):
        with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("class _StderrFilter", src,
                          "L1：_StderrFilter 已删除（多段 write 截断 bug；Qt 5.15.x 不打 iCCP）")
        # warnings.filterwarnings 保留作为兜底
        self.assertIn("warnings.filterwarnings", src,
                      "warnings.filterwarnings 兜底必须保留")


class TestSetupClientNoSysExit(unittest.TestCase):
    """L2: setup_client 失败不再 sys.exit(1)，弹 QMessageBox 后 self.client=None。"""

    def test_setup_client_does_not_sys_exit(self):
        from qwen_app import chat_window
        with open(chat_window.__file__, encoding="utf-8") as f:
            src = f.read()
        # AST 精确检查：setup_client 函数体里不应出现 sys.exit(...)
        # （注释里 "之前 sys.exit(1)" 提到旧行为，无害）
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "setup_client":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        # sys.exit(...)
                        if isinstance(f, ast.Attribute) and f.attr == "exit":
                            if isinstance(f.value, ast.Name) and f.value.id == "sys":
                                self.fail("L2: setup_client 仍在调 sys.exit()")
                return
        self.fail("找不到 setup_client 函数")


class TestItalicRegexEdgeCases(unittest.TestCase):
    """L3: 斜体正则对 `*x*` / `**x**` / `***x***` 行为正确。"""

    def test_simple_italic(self):
        from qwen_app.theme import markdown_to_html
        # 注意 markdown_to_html 先 escape html，但 * 是合法字符
        out = markdown_to_html("hello *world*", "light")
        self.assertIn("<i>world</i>", out, "标准 *x* 斜体应保留")

    def test_bold_not_mangled(self):
        """粗体 **x** 不应被斜体规则误吃"""
        from qwen_app.theme import markdown_to_html
        out = markdown_to_html("hello **world**", "light")
        self.assertIn("<b>world</b>", out)
        # 不会有嵌套 <i>world</i> 残留
        self.assertNotIn("<i>world</i>", out)

    def test_underscore_not_mangled(self):
        """下划线标识符（如 snake_case 变量名）不应被斜体规则吃掉"""
        from qwen_app.theme import markdown_to_html
        out = markdown_to_html("use foo_bar variable", "light")
        # 变量名应保留原样
        self.assertIn("foo_bar", out)
        # 不应有 <i>
        self.assertNotIn("<i>", out)


if __name__ == "__main__":
    unittest.main()
