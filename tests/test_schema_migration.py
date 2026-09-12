"""A2 回归测试：SQLite schema 迁移机制。

覆盖：
1. 全新数据库：init_conversations_db 后 user_version == SCHEMA_VERSION
2. 老数据库（user_version 存了 current_id）：_migrate_legacy_user_version
   把它迁到 session_state 表，并把 user_version 重置为 0
3. 注册到 SCHEMA_VERSION 之后，_run_migrations 自动跑完
4. 重复调用 init_conversations_db 是幂等的
"""
import os
import sys
import sqlite3
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestSchemaMigration(unittest.TestCase):
    """A2: schema_version 迁移链"""

    def setUp(self):
        # 用临时 db 替换项目 db（避免污染真实数据）
        self._tmpdir = tempfile.mkdtemp()
        self._tmpdb = os.path.join(self._tmpdir, "test_migration.db")

        # monkey-patch config 里的路径
        from qwen_app import config
        self._orig_db = config.CONVERSATIONS_DB
        config.CONVERSATIONS_DB = self._tmpdb
        # 同时清掉 _local.conn cache
        if hasattr(config._local, "conn"):
            try:
                config._local.conn.close()
            except Exception:
                pass
            delattr(config._local, "conn")
        self._config = config

    def tearDown(self):
        from qwen_app import config
        try:
            if hasattr(config._local, "conn"):
                config._local.conn.close()
        except Exception:
            pass
        if hasattr(config._local, "conn"):
            delattr(config._local, "conn")
        config.CONVERSATIONS_DB = self._orig_db
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_fresh_db_reaches_current_version(self):
        """全新数据库：init_conversations_db 后 user_version == SCHEMA_VERSION"""
        cfg = self._config
        cfg.init_conversations_db()
        cur = cfg._get_schema_version(cfg._get_db())
        self.assertEqual(cur, cfg.SCHEMA_VERSION,
                         f"全新数据库应自动跑到 v{cfg.SCHEMA_VERSION}")

    def test_legacy_user_version_migrated(self):
        """老数据库：user_version 存了 current_id（hex 整数）→ 迁到 session_state"""
        cfg = self._config
        # 模拟老数据库：手动建空 conversations + session_state 表，
        # 然后 user_version 设一个 hex 短串（模拟老 current_id 存储方式）
        conn = sqlite3.connect(self._tmpdb)
        conn.execute("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新对话',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE session_state (
                id INTEGER PRIMARY KEY CHECK (id=1), current_id TEXT
            )
        """)
        conn.execute("INSERT OR IGNORE INTO session_state (id, current_id) VALUES (1, NULL)")
        # 插入一行 id='abcdef' 的对话（确保 hex 还原后能匹配上）
        conn.execute("""
            INSERT INTO conversations (id, title, history, created_at, updated_at)
            VALUES ('abcdef', '老对话', '[]', '2026-01-01T00:00:00', '2026-01-01T00:00:00')
        """)
        conn.commit()
        # 老方式：current_id="abcdef" 存为 int("abcdef", 16) = 11259375 到 user_version
        # _migrate_legacy_user_version 应把它恢复成 "abcdef" 写到 session_state
        legacy_id = "abcdef"
        ver = int(legacy_id, 16) & 0x7FFFFFFF
        conn.execute(f"PRAGMA user_version={ver}")
        conn.close()

        # 现在跑 init_conversations_db
        cfg.init_conversations_db()

        db = cfg._get_db()
        cur = cfg._get_schema_version(db)
        # 应该是 SCHEMA_VERSION（_run_migrations 跑完后）
        self.assertEqual(cur, cfg.SCHEMA_VERSION,
                          f"迁移后应等于 v{cfg.SCHEMA_VERSION}，实际 v{cur}")
        # 恢复的 current_id 正确
        row = db.execute("SELECT current_id FROM session_state WHERE id=1").fetchone()
        self.assertEqual(row["current_id"], legacy_id,
                          f"老 current_id 应被恢复到 session_state，实际 {row['current_id']}")

    def test_legacy_user_version_fallback_to_first(self):
        """老 user_version 还原后匹配不到 conversation → 退到 conversations 第一条"""
        cfg = self._config
        conn = sqlite3.connect(self._tmpdb)
        conn.execute("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新对话',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE session_state (
                id INTEGER PRIMARY KEY CHECK (id=1), current_id TEXT
            )
        """)
        conn.execute("INSERT OR IGNORE INTO session_state (id, current_id) VALUES (1, NULL)")
        for cid, ts in [("first", "2026-01-01T00:00:00"),
                         ("middle", "2026-01-02T00:00:00"),
                         ("latest", "2026-01-03T00:00:00")]:
            conn.execute(
                "INSERT INTO conversations (id, title, history, created_at, updated_at) "
                "VALUES (?, ?, '[]', ?, ?)",
                (cid, cid, ts, ts),
            )
        # 老 user_version 还原成 "abcdef" 匹配不到任何行
        ver = int("abcdef", 16) & 0x7FFFFFFF
        conn.execute(f"PRAGMA user_version={ver}")
        conn.commit()
        conn.close()

        cfg.init_conversations_db()
        db = cfg._get_db()
        row = db.execute("SELECT current_id FROM session_state WHERE id=1").fetchone()
        self.assertEqual(row["current_id"], "latest",
                          f"匹配不到时回退到 conversations 第一条（updated_at DESC），"
                          f"实际 {row['current_id']}")

    def test_idempotent(self):
        """重复调用 init_conversations_db 不应出问题"""
        cfg = self._config
        cfg.init_conversations_db()
        cfg.init_conversations_db()
        cfg.init_conversations_db()
        cur = cfg._get_schema_version(cfg._get_db())
        self.assertEqual(cur, cfg.SCHEMA_VERSION)

    def test_migration_chain_registered(self):
        """_MIGRATIONS 字典必须有序且含当前版本"""
        cfg = self._config
        # 当前 SCHEMA_VERSION 必须在 _MIGRATIONS 里
        self.assertIn(cfg.SCHEMA_VERSION, cfg._MIGRATIONS,
                       f"v{cfg.SCHEMA_VERSION} 必须注册到 _MIGRATIONS")

    def test_get_set_schema_version(self):
        """_get_schema_version / _set_schema_version 配对工作"""
        cfg = self._config
        cfg.init_conversations_db()
        db = cfg._get_db()
        cfg._set_schema_version(db, 0)
        self.assertEqual(cfg._get_schema_version(db), 0)
        cfg._set_schema_version(db, 5)
        self.assertEqual(cfg._get_schema_version(db), 5)
        cfg._set_schema_version(db, cfg.SCHEMA_VERSION)
        self.assertEqual(cfg._get_schema_version(db), cfg.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
