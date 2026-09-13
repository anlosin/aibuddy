"""Day 19 (M-NEW-4): chat_bridge.list_readable_ext() + QML 调用一致性测试。

背景：QML DropArea.onDropped 之前自己维护一份 textExts 数组，与 Python
_READABLE_EXT 重复。M-NEW-4 修复后：
- chat_bridge 新增 list_readable_ext() 返回 _READABLE_EXT
- Main.qml 改为调用 bridge.list_readable_ext()
- 本测试验证：
  1. slot 返回值是 list（QVariantList）
  2. 内容与 _READABLE_EXT 同步（单一来源）
  3. 拒绝对应 .exe / .bin（不在白名单）+ 允许 .py / .md（在白名单）
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")


class TestListReadableExtSlot(unittest.TestCase):
    """M-NEW-4: chat_bridge.list_readable_ext() 必须暴露 _READABLE_EXT。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        cls.bridge = ChatBridge(theme="light")

    def test_returns_list(self):
        """slot 返回值必须是 list（PyQt5 自动转换 QVariantList）"""
        result = self.bridge.list_readable_ext()
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0, "白名单不应为空")

    def test_content_matches_readable_ext(self):
        """返回值必须与 _READABLE_EXT 完全一致（单一来源）"""
        result = self.bridge.list_readable_ext()
        self.assertEqual(sorted(result), sorted(self.bridge._READABLE_EXT),
                         "list_readable_ext 必须原样返回 _READABLE_EXT")

    def test_contains_python(self):
        result = self.bridge.list_readable_ext()
        self.assertIn(".py", result, ".py 必须在白名单内")

    def test_contains_markdown(self):
        result = self.bridge.list_readable_ext()
        self.assertIn(".md", result, ".md 必须在白名单内")

    def test_excludes_exe(self):
        """Day 18 C3 安全：.exe 不应在白名单（可执行文件被拒）"""
        result = self.bridge.list_readable_ext()
        self.assertNotIn(".exe", result, ".exe 必须不在白名单")

    def test_excludes_bin(self):
        """二进制 .bin 不在白名单（拖入 QML 也不会被 read_text_file）"""
        result = self.bridge.list_readable_ext()
        self.assertNotIn(".bin", result, ".bin 必须不在白名单")


class TestQmlUsesBridgeSlot(unittest.TestCase):
    """M-NEW-4: Main.qml 必须调用 bridge.list_readable_ext()，不能自己维护 textExts。"""

    def test_no_local_textExts_array_in_qml(self):
        """Main.qml 不应再含 textExts 数组（之前是硬编码本地数组）"""
        with open(MAIN_QML, encoding="utf-8") as f:
            src = f.read()
        # 之前的写法：const textExts = [".txt", ".md", ...]
        self.assertNotIn(
            'const textExts = [".txt"',
            src,
            "M-NEW-4: Main.qml 不应再硬编码 textExts 数组（已统一到 bridge.list_readable_ext）",
        )

    def test_qml_calls_bridge_list_readable_ext(self):
        """Main.qml 必须出现 bridge.list_readable_ext() 调用"""
        with open(MAIN_QML, encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            "bridge.list_readable_ext()",
            src,
            "M-NEW-4: Main.qml 必须调用 bridge.list_readable_ext()",
        )


class TestReadTextFileAcceptsAllowedAndRejectsBanned(unittest.TestCase):
    """M-NEW-4 验证（白名单合并后行为不变）：
    - .py / .md 在白名单内 → read_text_file 接受
    - .exe / .bin 不在白名单 → read_text_file 拒绝
    """

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        cls.bridge = ChatBridge(theme="light")
        import tempfile
        cls._tmpdir = tempfile.mkdtemp()
        cls._old_cwd = os.getcwd()
        os.chdir(cls._tmpdir)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._old_cwd)
        import shutil
        try:
            shutil.rmtree(cls._tmpdir)
        except Exception:
            pass

    def test_py_accepted(self):
        with open("ok.py", "w", encoding="utf-8") as f:
            f.write("print('hi')\n")
        result = self.bridge.read_text_file("ok.py")
        self.assertNotIn("不支持的文件类型", result)
        self.assertIn("print('hi')", result)

    def test_md_accepted(self):
        with open("doc.md", "w", encoding="utf-8") as f:
            f.write("# Title\n")
        result = self.bridge.read_text_file("doc.md")
        self.assertNotIn("不支持的文件类型", result)

    def test_exe_rejected(self):
        """Day 19 P2: 拖入 .exe 应被拒（白名单合并后仍然拒绝）"""
        with open("setup.exe", "wb") as f:
            f.write(b"MZ\x90\x00")
        result = self.bridge.read_text_file("setup.exe")
        self.assertIn("不支持的文件类型", result)

    def test_bin_rejected(self):
        with open("data.bin", "wb") as f:
            f.write(b"\x00\x01\x02\x03")
        result = self.bridge.read_text_file("data.bin")
        self.assertIn("不支持的文件类型", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
