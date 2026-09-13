"""Day 19 (新加): qwen_app._safe_path.is_safe_relative 单元测试。

覆盖：绝对路径 / POSIX 风格 / 相对 / 空 / 含盘符（Windows）/ Unicode /
正常文件名 / 子目录路径 都正确判断。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestIsSafeRelative(unittest.TestCase):
    """_safe_path.is_safe_relative 必须正确区分"安全相对路径"与"应拒绝"。"""

    @classmethod
    def setUpClass(cls):
        from qwen_app import _safe_path
        # 注：不能 cls.is_safe = func，因为描述符协议会让 self.is_safe 变成
        # bound method（self 作为第一个参数）。直接 import 后用 module.func。
        cls._mod = _safe_path

    def test_empty_string_rejected(self):
        self.assertFalse(self._mod.is_safe_relative(""))

    def test_none_rejected(self):
        self.assertFalse(self._mod.is_safe_relative(None))

    def test_simple_relative_filename(self):
        self.assertTrue(self._mod.is_safe_relative("hello.py"))

    def test_relative_subdir(self):
        self.assertTrue(self._mod.is_safe_relative("src/foo/bar.py"))

    def test_relative_with_dots(self):
        """./foo 或 ../foo 应被允许（语法上仍是相对路径）"""
        self.assertTrue(self._mod.is_safe_relative("./foo.py"))
        self.assertTrue(self._mod.is_safe_relative("../foo.py"))

    def test_unix_absolute_rejected(self):
        """POSIX 风格 /etc/passwd 在任何平台都必须拒"""
        self.assertFalse(self._mod.is_safe_relative("/etc/passwd"))
        self.assertFalse(self._mod.is_safe_relative("/tmp/secrets.txt"))
        self.assertFalse(self._mod.is_safe_relative("/"))

    def test_windows_absolute_rejected(self):
        """Windows 风格 C:\\Users\\... 在 Windows 上 os.path.isabs == True"""
        self.assertFalse(self._mod.is_safe_relative("C:\\Users\\test\\.ssh\\id_rsa"))
        self.assertFalse(self._mod.is_safe_relative("D:\\path\\to\\file.py"))

    def test_windows_unc_rejected(self):
        """Windows UNC 路径 \\\\server\\share 也应拒（os.path.isabs == True）"""
        self.assertFalse(self._mod.is_safe_relative("\\\\server\\share\\file.txt"))

    def test_filename_with_dot_prefix(self):
        """以 . 开头但仍是相对路径（点文件）应允许"""
        self.assertTrue(self._mod.is_safe_relative(".env"))
        self.assertTrue(self._mod.is_safe_relative(".gitignore"))

    def test_unicode_relative(self):
        self.assertTrue(self._mod.is_safe_relative("中文文件名.txt"))
        self.assertTrue(self._mod.is_safe_relative("文档/草稿.md"))

    def test_path_traversal_allowed_at_string_level(self):
        """路径遍历是字符串层面的事 —— is_safe 只看形态，不禁止 ../。
        实际防路径遍历是调用方（open）的责任（os.path.realpath 后判断
        是否逃出 working dir）。"""
        self.assertTrue(self._mod.is_safe_relative("../../etc/passwd"))


class TestChatBridgeUsesSafePath(unittest.TestCase):
    """chat_bridge 必须调 _safe_path.is_safe_relative，不再重复实现路径检查。"""

    def test_chat_bridge_imports_is_safe_relative(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from ._safe_path import is_safe_relative", src,
                      "chat_bridge 必须 import is_safe_relative")
        # 至少三处调用：read_text_file / _build_user_content / get_file_size
        count = src.count("is_safe_relative(")
        self.assertGreaterEqual(
            count, 3,
            f"chat_bridge 应至少 3 处用 is_safe_relative（实际 {count}）")

    def test_no_inline_absolute_path_check_in_chat_bridge(self):
        """旧版 `os.path.isabs(...) or path.startswith("/")` 必须消失"""
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # 搜 "os.path.isabs(file_path)" 这种直接 in-place 检查
        self.assertNotIn(
            "os.path.isabs(file_path) or file_path.startswith(\"/\")",
            src,
            "chat_bridge 不应再 inline 实现 isabs + startswith(\"/\") 检查（DRY）",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
