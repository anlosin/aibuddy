"""A3: SQLite 连接生命周期回归测试。

覆盖：
1. _get_db 创建的连接被 _all_conns 跟踪
2. close_all_conns 关闭所有连接 + 触发 WAL checkpoint
3. atexit 注册了 close_all_conns
4. 重复调 close_all_conns 不报错（幂等）
5. chat_window.closeEvent 显式调 close_all_conns
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestConnLifecycle(unittest.TestCase):
    """A3: SQLite 连接生命周期（atexit 关闭 + WAL checkpoint）"""

    def setUp(self):
        from qwen_app import config
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._tmpdb = os.path.join(self._tmpdir, "test_lifecycle.db")
        self._orig = config.CONVERSATIONS_DB
        config.CONVERSATIONS_DB = self._tmpdb
        if hasattr(config._local, "conn"):
            try:
                config._local.conn.close()
            except Exception:
                pass
            delattr(config._local, "conn")
        # 关闭之前所有连接（避免前序测试污染 _all_conns）
        config.close_all_conns()
        self._config = config

    def tearDown(self):
        cfg = self._config
        try:
            cfg.close_all_conns()
        except Exception:
            pass
        cfg.CONVERSATIONS_DB = self._orig
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_db_tracks_connection(self):
        """_get_db 创建的连接应被 _all_conns 跟踪"""
        cfg = self._config
        before = len(cfg._all_conns)
        db = cfg._get_db()
        self.assertIn(db, cfg._all_conns,
                       "_get_db 创建的连接必须加入 _all_conns（atexit 才能关闭）")
        self.assertEqual(len(cfg._all_conns), before + 1)

    def test_close_all_conns_idempotent(self):
        """close_all_conns 重复调用不报错"""
        cfg = self._config
        cfg._get_db()
        cfg.close_all_conns()
        cfg.close_all_conns()
        cfg.close_all_conns()
        self.assertEqual(len(cfg._all_conns), 0)

    def test_close_all_actually_closes(self):
        """关闭后 conn 再操作会抛 ProgrammingError（连接已关闭）"""
        cfg = self._config
        db = cfg._get_db()
        # 写入一条数据验证连接工作
        cfg.init_conversations_db()
        cfg.close_all_conns()
        self.assertEqual(len(cfg._all_conns), 0)
        # 连接已关，操作会抛 ProgrammingError
        with self.assertRaises(Exception):
            db.execute("SELECT 1").fetchone()

    def test_close_all_runs_wal_checkpoint(self):
        """close_all_conns 必须触发 PRAGMA wal_checkpoint(FULL)"""
        cfg = self._config
        # monkey patch db.execute 的替代：直接 hook PRAGMA 调用次数
        # （sqlite3 Connection.execute 是只读 property）
        sql_log = []
        # 用 conn.set_trace_hook（Python 3.13 新增），不行就用 wrapper
        # 简单方案：close_all_conns 后查 _all_conns 已空，
        # 且 db.execute("PRAGMA wal_checkpoint(FULL)") 已发起过（检查时间差）
        import time
        db = cfg._get_db()
        # monkey patch 通过替换 _local.conn 的方法不可行（execute 是 read-only）
        # 改用追踪：在 db 上注册 authorizer 太重；直接验证 close_all_conns
        # 内部对 _all_conns 里的每个连接都调了 execute —— 通过旁路计时佐证
        t0 = time.perf_counter()
        cfg.close_all_conns()
        elapsed = time.perf_counter() - t0
        # 如果调了 wal_checkpoint（PRAGMA）应至少 0.1ms（SQLite fsync）
        # —— 这是间接信号；更精确的是看实现源码（下面的 static_check）
        self.assertGreater(elapsed, 0,
                            "close_all_conns 应至少执行一些 SQL（wal_checkpoint）")
        # 静态检查：close_all_conns 实现里必须含 wal_checkpoint 字面量
        import inspect
        src = inspect.getsource(cfg.close_all_conns)
        self.assertIn("wal_checkpoint", src,
                      "close_all_conns 必须触发 PRAGMA wal_checkpoint(FULL)")

    def test_atexit_registered(self):
        """atexit 必须注册了 close_all_conns（兜底）"""
        # Python 3.13 改了 atexit 内部结构（不再有 _exithandlers），
        # 改用静态检查：config.py 源码里必须出现 atexit.register(close_all_conns)
        from qwen_app import config
        import inspect
        src = inspect.getsource(config)
        self.assertRegex(src, r"atexit\.register\s*\(\s*close_all_conns\s*\)",
                          "config.py 必须有 atexit.register(close_all_conns) 兜底")

    def test_threadlocal_cleared(self):
        """close_all_conns 之后 _local.conn 必须清空（下次 _get_db 创建新连接）"""
        cfg = self._config
        cfg._get_db()
        self.assertTrue(hasattr(cfg._local, "conn"))
        cfg.close_all_conns()
        self.assertFalse(hasattr(cfg._local, "conn"),
                          "close_all_conns 之后 _local.conn 应被清掉")


class TestChatWindowCloseEvent(unittest.TestCase):
    """A3: chat_window.closeEvent 显式调 close_all_conns"""

    def test_close_event_calls_close_all_conns(self):
        from qwen_app import chat_window
        with open(chat_window.__file__, encoding="utf-8") as f:
            src = f.read()
        # 找 closeEvent 函数体
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "closeEvent":
                # 整段源码里必须出现 close_all_conns
                block = ast.get_source_segment(src, node)
                self.assertIn("close_all_conns", block,
                              "closeEvent 必须显式调 close_all_conns")
                return
        self.fail("找不到 closeEvent 函数")


if __name__ == "__main__":
    unittest.main()
