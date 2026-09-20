# -*- coding: utf-8 -*-
"""Day 20.6.20 护栏：主题持久化（重启不再回浅色）。

背景：theme.py 有完整主题定义，但 set_theme 只改内存 + emit，
切换不落盘 → 重启永远回浅色。

实现（与 current_expert 同模式）：
- set_theme：load_config → cfg["theme"]=name → save_config
- ChatBridge(theme=None)：__init__ 读 cfg["theme"]（缺省 light）
- 显式传 theme（测试/探针）→ 按传入值，不读配置（确定性）

隔离：CONFIG_PATH 与 db 都重定向到 tmp。
"""
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication

_iso_tmp = None


def setUpModule():
    global _iso_tmp
    from qwen_app import config as _cfg
    _iso_tmp = tempfile.mkdtemp(prefix="iso_theme_")
    _cfg.CONFIG_PATH = os.path.join(_iso_tmp, "model_config.json")
    _cfg.set_db_path_for_tests(os.path.join(_iso_tmp, "conversations.db"))


def tearDownModule():
    from qwen_app import config as _cfg
    try:
        _cfg.close_all_conns()
    except Exception:
        pass
    _cfg.set_db_path_for_tests(None)
    if _iso_tmp:
        shutil.rmtree(_iso_tmp, ignore_errors=True)


class ThemePersistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        from qwen_app import config as _cfg
        # 每个用例从干净配置开始（unittest 按字母序跑，上一个用例可能
        # 已把 theme=dark 写进共享 tmp 配置 → 本用例 init 就读到 dark）
        try:
            os.remove(_cfg.CONFIG_PATH)
        except FileNotFoundError:
            pass
        from qwen_app.chat_bridge import ChatBridge
        self.events = []
        self.bridge = ChatBridge()   # theme=None → 走持久化读取路径
        self.bridge.themeChanged.connect(lambda n: self.events.append(n))

    def _saved_theme(self):
        from qwen_app import config as _cfg
        return _cfg.load_config().get("theme")

    def test_default_is_light_when_no_config(self):
        """无配置 → 默认 light"""
        self.assertEqual(self.bridge.get_theme(), "light")

    def test_set_theme_persists(self):
        """set_theme("dark") → 内存 + 落盘 + emit 三件套"""
        self.bridge.set_theme("dark")
        self.assertEqual(self.bridge.get_theme(), "dark")
        self.assertEqual(self._saved_theme(), "dark")
        self.assertEqual(self.events, ["dark"])

    def test_persisted_theme_restored_on_restart(self):
        """重启模拟：新 bridge（theme=None）读到上次保存的 dark"""
        self.bridge.set_theme("dark")
        from qwen_app.chat_bridge import ChatBridge
        b2 = ChatBridge()
        self.assertEqual(b2.get_theme(), "dark", "重启后必须恢复上次主题")

    def test_explicit_theme_arg_wins_over_config(self):
        """显式传入 theme（测试/探针路径）→ 不读配置，保持确定性"""
        self.bridge.set_theme("dark")
        from qwen_app.chat_bridge import ChatBridge
        b2 = ChatBridge(theme="light")
        self.assertEqual(b2.get_theme(), "light")

    def test_invalid_theme_ignored(self):
        """非法值不改内存、不落盘、不 emit"""
        self.bridge.set_theme("blue")
        self.assertEqual(self.bridge.get_theme(), "light")
        self.assertIsNone(self._saved_theme())
        self.assertEqual(self.events, [])

    def test_set_same_theme_is_noop(self):
        """重复设置当前主题 → 不重复落盘 emit"""
        self.bridge.set_theme("dark")
        self.bridge.set_theme("dark")
        self.assertEqual(self.events, ["dark"])


if __name__ == "__main__":
    unittest.main()
