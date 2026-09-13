"""Day 19 (M-NEW-6): config.close_all_conns 重复调用必须幂等。

背景：atexit 在进程退出兜底跑一次，chat_window.closeEvent 也调
close_all_conns，顺序不定。窗口先关 → atexit 再跑：第二次 pop 同一连接
会触发 db.execute("PRAGMA wal_checkpoint(FULL)") 抛
sqlite3.ProgrammingError: Cannot operate on a closed database，进程
带着 traceback 闪退。修复：close_all_conns 内部对已关闭连接用
"SELECT 1" 探活，ProgrammingError 直接跳过。
"""
import os
import sqlite3
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestCloseAllConnsIdempotent(unittest.TestCase):
    """M-NEW-6: close_all_conns 必须可以连续调用多次而不报错。"""

    def setUp(self):
        """每个测试前重置 _all_conns 和 _local.conn（不影响真实 DB）"""
        from qwen_app import config
        # 复制一份做快照，测试结束恢复（避免污染其他测试）
        self._orig_all_conns = set(config._all_conns)
        self._orig_local_conn = getattr(config._local, "conn", None)
        config._all_conns.clear()
        # 清掉 thread-local 缓存
        if hasattr(config._local, "conn"):
            delattr(config._local, "conn")

    def tearDown(self):
        from qwen_app import config
        config._all_conns.clear()
        if hasattr(config._local, "conn"):
            delattr(config._local, "conn")

    def _make_real_conn(self):
        """造一个真实 SQLite 连接并注入 _all_conns（模拟 _get_db 流程）"""
        from qwen_app import config
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        config._all_conns.add(db)
        return db

    def test_three_calls_no_error(self):
        """连续 3 次调用 close_all_conns 不抛任何异常"""
        from qwen_app import config
        self._make_real_conn()
        # 第一次：关
        config.close_all_conns()
        # 第二次：连接已关，应跳过而非抛 ProgrammingError
        config.close_all_conns()
        # 第三次：仍然要安全
        config.close_all_conns()
        # 走到这里没异常 = 通过

    def test_close_then_close_all_no_error(self):
        """先 db.close() 手动关，再 close_all_conns 不报错"""
        from qwen_app import config
        db = self._make_real_conn()
        db.close()
        config.close_all_conns()  # 不应抛 ProgrammingError

    def test_uses_programmingerror_skip(self):
        """代码必须用 try db.execute("SELECT 1") except ProgrammingError 探活"""
        from qwen_app import config
        import inspect
        src = inspect.getsource(config.close_all_conns)
        # 必须有 SELECT 1 探活 + ProgrammingError 处理
        self.assertIn('"SELECT 1"', src,
                      "M-NEW-6: close_all_conns 必须用 SELECT 1 探活")
        self.assertIn("ProgrammingError", src,
                      "M-NEW-6: 必须捕获 sqlite3.ProgrammingError 跳过已关闭连接")

    def test_empty_set_no_error(self):
        """空 _all_conns 时调用也必须安全"""
        from qwen_app import config
        config._all_conns.clear()
        config.close_all_conns()
        config.close_all_conns()


if __name__ == "__main__":
    unittest.main(verbosity=2)
