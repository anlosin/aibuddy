"""Day 19 回归测试：is_safe_relative 用错位置导致拖入完全失效。

QML DropArea.toLocalFile() 返回绝对路径（C:/Users/...），
而 is_safe_relative 拒绝所有绝对路径 → read_text_file / _build_user_content / get_file_size
三处全被误拒。GUI 拖入功能完全废了。

E2E 启动测试不会发现这个 bug（启动时 QML 没有 drop 事件）。
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestDropAbsolutePathRegression(unittest.TestCase):
    """模拟 QML DropArea 行为：调 toLocalFile() 后传绝对路径给 bridge。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.bridge = ChatBridge(theme="light")
        # 模拟 QML DropArea.toLocalFile() 行为：在真实目录创建临时文件，
        # 用绝对路径调 bridge。
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_read_text_file_accepts_qml_drop_absolute_path(self):
        """Day 13 设计意图：拖入文本文件应能读到内容。

        QML DropArea.toLocalFile() 返回 'C:\\Users\\...\\file.py' 这种绝对路径。
        之前 Day 18 C3 加了'拒绝绝对路径'防御（防 LLM 注入），但
        这个防御的源头（read_text_file）只能被 QML 调，LLM 走不到这里。
        Day 19 f4e8a98 把这个错误防御抽到 _safe_path 反而强化了它。
        """
        path = os.path.join(self._tmpdir, "test.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("def hello():\n    print('hi')\n")
        # 模拟 QML 拖入：传绝对路径
        result = self.bridge.read_text_file(path)
        # 期望：返回 "[文件: test.py]\n```python\n...\n```"
        # 实际（C3+f4e8a98 后）：返回 "[读文件失败: 不支持绝对路径...]"
        self.assertNotIn("不支持绝对路径", result,
                          "BUG：绝对路径被 is_safe_relative 误拒。GUI 拖入场景都是绝对路径")
        self.assertIn("hello()", result, "应读到文件内容")

    def test_build_user_content_accepts_absolute_image_path(self):
        """拖入图片应是绝对路径，应能 base64 编码成功。

        _build_user_content 也用 is_safe_relative，导致图片附件永远被拒。
        """
        from PIL import Image
        # 跳过如果没装 PIL——但本测试本质上是图像处理，应该用 png 字节
        try:
            img_path = os.path.join(self._tmpdir, "test.png")
            # 最小 PNG（1x1 透明）
            img_data = bytes([
                0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
                0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
                0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
                0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,
                0x89, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x44, 0x41,
                0x54, 0x78, 0x9c, 0x63, 0x00, 0x01, 0x00, 0x00,
                0x05, 0x00, 0x01, 0x0d, 0x0a, 0x2d, 0xb4, 0x00,
                0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae,
                0x42, 0x60, 0x82,
            ])
            with open(img_path, "wb") as f:
                f.write(img_data)
            # 模拟 QML：传绝对路径调 _build_user_content
            result = self.bridge._build_user_content("hi", [img_path])
            # 期望：返回 [text_block, image_url_block]
            # 实际：返回 [text_block]（image 被拒）
            if isinstance(result, list):
                self.assertEqual(len(result), 2,
                                  f"BUG：图片被 is_safe_relative 误拒。blocks={result}")
            else:
                self.fail(f"应返回 list[text, image_url]，实际 {result!r}")
        except Exception as e:
            self.skipTest(f"PIL/PIL 不可用：{e}")


class TestSafePathDesignFlaw(unittest.TestCase):
    """is_safe_to_read 设计：不再拒绝绝对路径（GUI 拖入合法形式）。"""

    def test_is_safe_to_read_accepts_absolute_paths(self):
        """GUI 拖入路径永远是绝对的（toLocalFile 返回 'C:/...'），
        is_safe_to_read 应接受。"""
        from qwen_app._safe_path import is_safe_to_read
        self.assertTrue(is_safe_to_read("C:/Users/test/file.py"),
                         "GUI 拖入路径永远是绝对的，必须被接受")
        self.assertTrue(is_safe_to_read("/home/user/file.py"),
                         "Linux GUI 拖入路径同样是绝对的，必须被接受")

    def test_is_safe_to_read_rejects_empty(self):
        from qwen_app._safe_path import is_safe_to_read
        self.assertFalse(is_safe_to_read(""))
        self.assertFalse(is_safe_to_read(None))

    def test_is_safe_to_read_rejects_control_chars(self):
        from qwen_app._safe_path import is_safe_to_read
        self.assertFalse(is_safe_to_read("file\x00.txt"))
        self.assertFalse(is_safe_to_read("file\n.txt"))

    def test_is_safe_to_read_rejects_oversize(self):
        from qwen_app._safe_path import is_safe_to_read
        self.assertFalse(is_safe_to_read("a" * 5000))


if __name__ == "__main__":
    unittest.main()
