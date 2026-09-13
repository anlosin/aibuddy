"""Day 19 (H-NEW-8): chat_bridge.get_file_size() 单元测试 + QML 调用校验。

背景：用户可能误拖 ISO / 视频 / 大压缩包进 QML DropArea。chat_bridge
自身 read_text_file 限 50KB、图片附件限 4MB，但"无意义大文件"应在
DropArea.onDropped 早期就被拒绝 + 提示，避免后面 read / 编码浪费 CPU。

测试覆盖：
1. get_file_size 返回 QVariantMap 含 ok / size / error
2. 正常文件返回 ok=True 且 size 正确
3. 不存在的文件 ok=False + error 含"不存在"
4. 空路径 ok=False
5. 绝对路径（含 POSIX 风格）被拒（与 read_text_file 路径策略一致）
6. QML 端必须调 bridge.get_file_size()（不再裸用 FileInfo）
7. >50MB 的文件 QML 端识别为大文件（50MB 上限静态检查）
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")


class TestGetFileSizeSlot(unittest.TestCase):
    """H-NEW-8: chat_bridge.get_file_size() 接口契约"""

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

    def test_returns_qvariantmap(self):
        """返回值必须是 dict（PyQt5 自动包装为 QVariantMap）"""
        result = self.bridge.get_file_size("anything.txt")
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)
        self.assertIn("size", result)
        self.assertIn("error", result)

    def test_existing_file_returns_size(self):
        """存在的相对路径文件 → ok=True + 正确字节数"""
        path = "small.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write("hello world")
        result = self.bridge.get_file_size(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["size"], 11)  # len("hello world") == 11
        self.assertEqual(result["error"], "")

    def test_empty_path_rejected(self):
        result = self.bridge.get_file_size("")
        self.assertFalse(result["ok"])
        self.assertIn("路径为空", result["error"])

    def test_nonexistent_file_rejected(self):
        result = self.bridge.get_file_size("ghost_xyz.txt")
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])

    def test_absolute_path_rejected(self):
        """绝对路径（含 POSIX 风格）被拒，与 read_text_file 策略一致"""
        # Windows 风格
        win_abs = os.path.join(os.getcwd(), "x.txt")
        result = self.bridge.get_file_size(win_abs)
        self.assertFalse(result["ok"])
        self.assertIn("绝对路径被拒绝", result["error"])
        # POSIX 风格
        result2 = self.bridge.get_file_size("/etc/passwd")
        self.assertFalse(result2["ok"])
        self.assertIn("绝对路径被拒绝", result2["error"])

    def test_50mb_threshold(self):
        """50MB 上限：正好 50MB 不应被视为过大（> 50MB 才拒）"""
        # 静态校验：QML 端的 maxFileBytes = 50*1024*1024
        with open(MAIN_QML, encoding="utf-8") as f:
            qml = f.read()
        self.assertIn("maxFileBytes", qml,
                      "H-NEW-8: Main.qml DropArea 必须有 maxFileBytes 属性")
        # 必须出现 50MB 数字字面量
        import re
        m = re.search(r"maxFileBytes:\s*50\s*\*\s*1024\s*\*\s*1024", qml)
        self.assertIsNotNone(
            m,
            "H-NEW-8: maxFileBytes 必须为 50*1024*1024（50MB），不能改小或改大")

    def test_qml_calls_get_file_size(self):
        """QML 端必须调用 bridge.get_file_size()，不能再裸读 FileInfo"""
        with open(MAIN_QML, encoding="utf-8") as f:
            qml = f.read()
        self.assertIn("bridge.get_file_size", qml,
                      "H-NEW-8: Main.qml DropArea.onDropped 必须调 bridge.get_file_size()")


if __name__ == "__main__":
    unittest.main(verbosity=2)
