"""session_state 表 + 旧 PRAGMA user_version 数据迁移的回归测试。

覆盖：
1. 首次启动：session_state 表自动创建，current_id 为 NULL
2. 保存/加载 current_id 不再丢字符（原 hex ID 完整保留，不再被 31 位整数截位）
3. 旧数据库（PRAGMA user_version 存了 current_id）能迁移：精确匹配则恢复，
   匹配不上则回退到第一条对话
4. PRAGMA user_version 现在装的是真实 schema version，不再是 current_id
"""
import os
import sqlite3
import sys
import tempfile
import unittest

# 把项目根加入 sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


class _IsolatedDB(unittest.TestCase):
    """每个用例用临时数据库，避免污染真实 data/."""

    def setUp(self):
        # 1) 备份当前线程的全局连接；2) 改 DATA_DIR/CONVERSATIONS_DB 指向 tmp
        import qwen_app.config as _cfg
        self._orig_data_dir = _cfg.DATA_DIR
        self._orig_db = _cfg.CONVERSATIONS_DB
        self._tmp = tempfile.mkdtemp()
        _cfg.DATA_DIR = self._tmp
        _cfg.CONVERSATIONS_DB = os.path.join(self._tmp, "conversations.db")
        # 让线程局部连接重新建
        _cfg._local = type(_cfg._local)()  # 新 thread.local，清旧连接

    def tearDown(self):
        import qwen_app.config as _cfg
        _cfg.DATA_DIR = self._orig_data_dir
        _cfg.CONVERSATIONS_DB = self._orig_db
        _cfg._local = type(_cfg._local)()
        try:
            import shutil
            shutil.rmtree(self._tmp, ignore_errors=True)
        except Exception:
            pass

    def _direct_db(self):
        import qwen_app.config as _cfg
        return sqlite3.connect(_cfg.CONVERSATIONS_DB)


class TestSessionStateTable(_IsolatedDB):
    def test_init_creates_table(self):
        from qwen_app import config
        config.init_conversations_db()
        rows = self._direct_db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in rows}
        self.assertIn("conversations", names)
        self.assertIn("session_state", names)

    def test_load_initially_returns_none_current(self):
        from qwen_app import config
        config.init_conversations_db()
        convs, cur = config.load_conversations()
        self.assertEqual(convs, [])
        self.assertIsNone(cur)

    def test_current_id_round_trip_preserves_full_id(self):
        """C1 修复点：原实现把 current_id 截到 31 位整数（&0x7FFFFFFF），
        截断后再用 hex() 还原，对长度 > 7 的 hex ID 会丢字符。
        现在必须完整保留。"""
        from qwen_app import config
        config.init_conversations_db()
        long_id = "abcdef0123456789"   # 16 字符 hex，远超 31 位整数可表示范围
        config.save_single_conversation(
            {"id": long_id, "title": "长 ID", "history": [], "created_at": "2026-01-01"},
            long_id,
        )
        _, cur = config.load_conversations()
        self.assertEqual(cur, long_id, "current_id 必须完整保留原 hex 字符串")

    def test_set_current_conversation(self):
        from qwen_app import config
        config.init_conversations_db()
        config.save_single_conversation(
            {"id": "aaaa", "title": "a", "history": [], "created_at": "2026-01-01"},
            "aaaa",
        )
        config.save_single_conversation(
            {"id": "bbbb", "title": "b", "history": [], "created_at": "2026-01-02"},
            "bbbb",
        )
        config.set_current_conversation("bbbb")
        _, cur = config.load_conversations()
        self.assertEqual(cur, "bbbb")
        # 切回 aaaa
        config.set_current_conversation("aaaa")
        _, cur = config.load_conversations()
        self.assertEqual(cur, "aaaa")

    def test_falls_back_to_first_when_current_deleted(self):
        """当前会话被删时回退到第一个，不崩。"""
        from qwen_app import config
        config.init_conversations_db()
        config.save_single_conversation(
            {"id": "keep1", "title": "k1", "history": [], "created_at": "2026-01-01"},
            "keep1",
        )
        config.save_single_conversation(
            {"id": "gone", "title": "g", "history": [], "created_at": "2026-01-02"},
            "gone",
        )
        # 删掉 gone
        config.save_conversations(
            [{"id": "keep1", "title": "k1", "history": [], "created_at": "2026-01-01"}],
            "keep1",
        )
        _, cur = config.load_conversations()
        self.assertEqual(cur, "keep1")

    def test_schema_version_uses_pragmas_real_value(self):
        """PRAGMA user_version 必须装 schema_version，不再装 current_id 整数。"""
        from qwen_app import config
        config.init_conversations_db()
        ver = self._direct_db().execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(ver, config.SCHEMA_VERSION)


class TestLegacyMigration(_IsolatedDB):
    """C1 迁移：旧版本用 PRAGMA user_version 存 current_id，模拟这种数据库。"""

    def _seed_legacy_db(self, conv_id_hex_str, raw_int=None):
        """模拟老数据库：conversations 表 + PRAGMA user_version。

        老实现 save_single_conversation 走：ver = int(current_id, 16) & 0x7FFFFFFF。
        这里 seed 一个 hex 字符串 id，并按老公式把 int 写到 PRAGMA。
        """
        if raw_int is None:
            raw_int = int(conv_id_hex_str, 16) & 0x7FFFFFFF
        db = self._direct_db()
        db.execute("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新对话',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        db.execute(
            "INSERT INTO conversations (id, title, history, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id_hex_str, "老会话", "[]", "2026-01-01", "2026-01-01"),
        )
        db.execute(f"PRAGMA user_version={raw_int}")
        db.commit()
        db.close()

    def test_exact_hex_match_migrates(self):
        """老 DB 的 PRAGMA user_version 还原成 hex 后能精确匹配到 conversations.id"""
        from qwen_app import config
        self._seed_legacy_db("abcd")
        convs, cur = config.load_conversations()
        self.assertEqual(cur, "abcd")
        ver = self._direct_db().execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(ver, config.SCHEMA_VERSION)

    def test_unmatched_hex_falls_back_to_first(self):
        """老 DB 的 PRAGMA 值反查不到（被截过/已删除）→ 回退第一条，不崩"""
        from qwen_app import config
        # seed 一个真实存在的 hex id，并写一个不匹配的 user_version
        self._seed_legacy_db("real-conv", raw_int=0xff)
        convs, cur = config.load_conversations()
        self.assertEqual(cur, "real-conv", "回退到第一条真实对话")

    def test_migration_idempotent(self):
        """多次 init 不会重复迁移、不会破坏数据。"""
        from qwen_app import config
        self._seed_legacy_db("1234")
        config.init_conversations_db()
        config.init_conversations_db()
        _, cur = config.load_conversations()
        self.assertEqual(cur, "1234")


if __name__ == "__main__":
    unittest.main()
