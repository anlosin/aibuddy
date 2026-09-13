"""Day 19 隐藏 bug：sql_helper PRAGMA query_only 状态泄漏到后续 write。

Day 18 C2 修复：read_only=True 时设 PRAGMA query_only=ON。
但 PRAGMA 是连接级状态，函数返回后 query_only 仍 ON。
下次 _do_query(read_only=False) 时同连接仍处于只读模式，
SQLite 拒绝 INSERT/UPDATE → silent failure。

严重性：高 —— LLM 通过 db_query 写入数据会静默失败，
插件作者排查时发现 INSERT 没生效，但没异常（SQLite 静默拒绝）。
"""
import os
import sys
import sqlite3
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestQueryOnlyLeak(unittest.TestCase):
    """验证 PRAGMA query_only 状态泄漏问题。"""

    def test_query_only_persists_after_first_call(self):
        """read_only=True 调一次后，同连接 query_only 仍为 ON
        （PRAGMA 是连接级状态，sqlite3.connect 同一 conn 仍生效）"""
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")

        # 设 PRAGMA query_only=ON，模拟 sql_helper read_only=True
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.execute("SELECT * FROM t")
        cur.fetchall()
        cur.close()

        # 验证 PRAGMA 状态
        cur_check = conn.cursor()
        cur_check.execute("PRAGMA query_only")
        qo = cur_check.fetchone()[0]
        cur_check.close()
        self.assertEqual(qo, 1, "query_only 应仍为 1（连接级状态泄漏）")

        # 验证 INSERT 被拒（SQLite 报 "attempt to write a readonly database"）
        cur_write = conn.cursor()
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            cur_write.execute("INSERT INTO t VALUES (2)")
        self.assertIn("readonly", str(ctx.exception).lower(),
                      f"SQLite 应报 readonly 错，实际：{ctx.exception}")
        cur_write.close()

        conn.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_sql_helper_query_only_not_reset(self):
        """直接测 sql_helper 行为：第一次 read_only=True 后，第二次 read_only=False 仍失败"""
        from plugins import sql_helper
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

        # 用 db_connect 注册连接
        r_conn = sql_helper.execute("db_connect", {
            "type": "sqlite",
            "path": db_path,
            "name": "test_db",
        })
        print(f"db_connect 返回: {r_conn[:80]!r}")

        # 第一次 read_only=True
        r1 = sql_helper.execute("db_query", {
            "name": "test_db",
            "sql": "SELECT * FROM t",
            "read_only": True,
        })
        print(f"第一次 SELECT: {r1[:80]!r}")

        # 第二次 read_only=False（应该能写）
        r2 = sql_helper.execute("db_query", {
            "name": "test_db",
            "sql": "INSERT INTO t VALUES (2)",
            "read_only": False,
        })
        print(f"INSERT 返回: {r2[:200]!r}")

        # 验证数据是否真的写入
        r3 = sql_helper.execute("db_query", {
            "name": "test_db",
            "sql": "SELECT * FROM t ORDER BY x",
            "read_only": True,
        })
        print(f"SELECT 验证: {r3[:200]!r}")

        sql_helper._get_conn("test_db")[0].close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
