"""Day 19.1 (修订): qwen_app._safe_path 单元测试。

设计意图修订：
- 旧 is_safe_relative 拒绝绝对路径是错误设计（GUI 拖入永远是绝对路径）
- 现在 is_safe_to_read() 只过滤 None/空/控制字符/超长
- is_safe_relative() 保留为 deprecated 兼容 API，永远返回 True（除空）

绝对路径由调用方扩展名白名单 + 大小限制防御。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestIsSafeToRead(unittest.TestCase):
    """新 API：is_safe_to_read 只做基础字符串过滤。"""

    def setUp(self):
        from qwen_app import _safe_path
        self._mod = _safe_path

    def test_rejects_empty(self):
        self.assertFalse(self._mod.is_safe_to_read(""))
        self.assertFalse(self._mod.is_safe_to_read(None))

    def test_accepts_absolute_path(self):
        """GUI 拖入路径永远绝对，必须接受。"""
        self.assertTrue(self._mod.is_safe_to_read("C:/Users/test/file.py"))
        self.assertTrue(self._mod.is_safe_to_read("/etc/passwd"))
        self.assertTrue(self._mod.is_safe_to_read("/tmp/secrets.txt"))
        self.assertTrue(self._mod.is_safe_to_read("C:\\Users\\test\\.ssh\\id_rsa"))
        self.assertTrue(self._mod.is_safe_to_read("\\\\server\\share\\file.txt"))

    def test_accepts_relative_path(self):
        self.assertTrue(self._mod.is_safe_to_read("hello.py"))
        self.assertTrue(self._mod.is_safe_to_read("src/foo/bar.py"))
        self.assertTrue(self._mod.is_safe_to_read("./foo.py"))
        self.assertTrue(self._mod.is_safe_to_read("../foo.py"))
        self.assertTrue(self._mod.is_safe_to_read(".env"))
        self.assertTrue(self._mod.is_safe_to_read("中文文件名.txt"))
        self.assertTrue(self._mod.is_safe_to_read("文档/草稿.md"))
        self.assertTrue(self._mod.is_safe_to_read("../../etc/passwd"))

    def test_rejects_control_chars(self):
        self.assertFalse(self._mod.is_safe_to_read("file\x00.txt"))
        self.assertFalse(self._mod.is_safe_to_read("file\n.txt"))
        self.assertFalse(self._mod.is_safe_to_read("file\r.txt"))
        self.assertFalse(self._mod.is_safe_to_read("file\t.txt"))

    def test_rejects_oversize(self):
        self.assertFalse(self._mod.is_safe_to_read("a" * 5000))
        self.assertTrue(self._mod.is_safe_to_read("a" * 3000))


class TestIsSafeRelativeBackwardCompat(unittest.TestCase):
    """旧 API 保留但永远 True（兼容历史 chat_bridge 调用）。"""

    def setUp(self):
        from qwen_app import _safe_path
        self._mod = _safe_path

    def test_relative_still_true(self):
        """旧 API 接受相对路径（兼容）。"""
        self.assertTrue(self._mod.is_safe_relative("hello.py"))
        self.assertTrue(self._mod.is_safe_relative("../foo.py"))

    def test_absolute_now_accepted(self):
        """Day 19.1 修订：绝对路径现在被接受（不再拒绝）。"""
        # 旧测试期望这里返回 False；现在修正为 True
        self.assertTrue(self._mod.is_safe_relative("/etc/passwd"))
        self.assertTrue(self._mod.is_safe_relative("C:\\Users\\test\\file.py"))

    def test_empty_still_false(self):
        """空路径仍拒。"""
        self.assertFalse(self._mod.is_safe_relative(""))
        self.assertFalse(self._mod.is_safe_relative(None))


class TestChatBridgeUsesSafePath(unittest.TestCase):
    """chat_bridge 必须用 _safe_path 工具（DRY，不再内联实现）。"""

    def test_chat_bridge_imports_safe_path(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from ._safe_path import", src,
                      "chat_bridge 必须 import _safe_path")

    def test_chat_bridge_uses_safe_path_in_3_places(self):
        """chat_bridge 至少有 3 处调用 _safe_path（read_text_file / _build_user_content / get_file_size）"""
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # 统计 is_safe_to_read( 或 is_safe_relative( 的调用次数
        count = src.count("is_safe_to_read(") + src.count("is_safe_relative(")
        self.assertGreaterEqual(count, 3,
            f"chat_bridge 应至少 3 处用 _safe_path（实际 {count}）")


if __name__ == "__main__":
    unittest.main()
