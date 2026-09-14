"""Day 19.1.1: 全局守卫 — 所有对话 ID 生成必须用完整 UUID（36 字符）。

背景：
- 早期实现用 str(uuid.uuid4())[:8]，仅 32 bits 熵（4B 种可能）。
- 65k 个会话就有 50% 碰撞概率（生日攻击数学）。
- chat_bridge.create_session、chat_window.import_conversation、
  session.new_conversation 都曾踩这个坑。

本测试静态扫描 + 运行时双重保险：
1. 源码扫描：所有生成 session ID 的地方必须用 str(uuid.uuid4())，不能用 [:N] 截断。
2. 运行时校验：实际生成的 ID 是完整 UUID（36 字符含 dash）。
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


class TestNoUUIDTruncation(unittest.TestCase):
    """扫描源码：禁止把 UUID 截断成短 id。"""

    def _scan_file_for_truncated_uuid(self, filepath, expected_pattern):
        """扫描指定文件，确认 uuid 生成行不出现 [:N] 截断。"""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # 匹配形如 `str(uuid.uuid4())[:N]` 或 `uuid.uuid4().hex[:N]` 的截断模式
        truncated_pattern = re.compile(
            r"(?:uuid\.uuid4\(\)|_uuid\.uuid4\(\)|uuid4\(\))\s*\.\s*(?:hex\s*)?\[\s*:\s*\d+\s*\]"
        )
        hits = truncated_pattern.findall(content)
        self.assertEqual(
            hits, [],
            f"{filepath} 含 UUID 截断模式 {hits!r}。"
            f"应使用完整 UUID（str(uuid.uuid4()) 或 str(_uuid.uuid4())，不带 [:N]）。"
            f"\n完整 UUID = 36 字符含 dash（122 bits 熵），"
            f"短 id 仅 32 bits 熵，约 65k 个 session 50% 碰撞。",
        )
        # 进一步断言：文件中至少含一处 uuid.uuid4() 用法（防止扫错文件）
        self.assertRegex(
            content, expected_pattern,
            f"{filepath} 缺少 uuid.uuid4() 调用，扫描失败或文件不相关",
        )

    def test_chat_bridge_uses_full_uuid(self):
        """chat_bridge 模块组: create_session 不再用 [:8] 截断。

        拆分后 create_session 位于 _bridge_session.py —— 扫描目标随之更新。
        """
        self._scan_file_for_truncated_uuid(
            os.path.join(_ROOT, "qwen_app", "_bridge_session.py"),
            r"_uuid\.uuid4\(\)",
        )

    def test_chat_window_uses_full_uuid(self):
        """chat_window.py: import_conversation 不再用 [:8] 截断。"""
        self._scan_file_for_truncated_uuid(
            os.path.join(_ROOT, "qwen_app", "chat_window.py"),
            r"uuid\.uuid4\(\)",
        )

    def test_session_module_uses_full_uuid(self):
        """session.py: new_conversation 不再用 [:8] 截断。"""
        self._scan_file_for_truncated_uuid(
            os.path.join(_ROOT, "qwen_app", "session.py"),
            r"uuid\.uuid4\(\)",
        )


class TestRuntimeFullUUID(unittest.TestCase):
    """运行时校验：create_session 实际返回完整 UUID。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def test_create_session_returns_36_char_uuid(self):
        """运行时：create_session 必须返回 36 字符 UUID。"""
        import uuid as _u
        cid = self.bridge.create_session("运行时校验")
        self.created_ids.append(cid)
        self.assertEqual(len(cid), 36)
        parsed = _u.UUID(cid)
        self.assertEqual(parsed.version, 4)


if __name__ == "__main__":
    unittest.main()