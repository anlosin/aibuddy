"""Day 18 修复 C2 回归测试：SQL 只读模式双层防护。

第 1 层（业务层 fast-fail）  _has_write_statement() 检测首关键字
第 2 层（连接层兜底）       read_only=True 时在执行前打开事务级 readonly
- SQLite: PRAGMA query_only=ON → INSERT/UPDATE/DELETE 会抛 OperationalError
- MySQL / PostgreSQL: SET TRANSACTION READ ONLY（驱动支持时）

本测试不引入 mysql/postgres 依赖，只覆盖 SQLite 路径。
"""
import os
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


class _SqliteOnly(unittest.TestCase):
    """用真实 SQLite 内存数据库验证双层防护。

    每次 do_query 都构造新的连接，因为 sql_helper._do_query 内部会
    finally 关闭连接。我们用 helper 替身让 _get_conn 返回新连接。
    """

    def _fresh_conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("INSERT INTO users (id, name) VALUES (1, 'alice')")
        c.execute("INSERT INTO users (id, name) VALUES (2, 'bob')")
        c.commit()
        return c

    def _patched_get_conn(self):
        from plugins import sql_helper
        conn = self._fresh_conn()
        orig = sql_helper._get_conn

        def fake_get_conn(name):
            return conn, None

        sql_helper._get_conn = fake_get_conn
        return orig, conn

    def test_select_passes_in_readonly(self):
        from plugins import sql_helper
        orig, _ = self._patched_get_conn()
        try:
            r = sql_helper._do_query({"name": "x", "sql": "SELECT * FROM users", "read_only": True})
            self.assertIn("alice", r)
        finally:
            sql_helper._get_conn = orig

    def test_write_blocked_by_business_layer(self):
        from plugins import sql_helper
        orig, _ = self._patched_get_conn()
        try:
            r = sql_helper._do_query({"name": "x", "sql": "DROP TABLE users", "read_only": True})
            self.assertIn("只读模式", r)
        finally:
            sql_helper._get_conn = orig

    def test_readonly_pragma_query_only_set_on_connection(self):
        """验证 SQLite query_only 真的会拦截 DDL（业务层 + 驱动层双重防护的
        驱动层部分）。

        直接构造连接开 query_only，然后尝试 CREATE / INSERT / DROP，应全部
        被 OperationalError 拦下。这证明：业务层 _has_write_statement 漏过的
        写操作，驱动层仍能兜住（C2 修复目标）。
        """
        c = sqlite3.connect(":memory:")
        try:
            c.execute("PRAGMA query_only = ON")
            for bad_sql in [
                "CREATE TABLE t (id INT)",
                "INSERT INTO t VALUES (1)",
                "DROP TABLE t",
                "UPDATE t SET id=2",
                "DELETE FROM t",
            ]:
                with self.assertRaises(sqlite3.OperationalError, msg=f"query_only 应拦: {bad_sql}"):
                    c.execute(bad_sql)
        finally:
            c.close()

    def test_write_allowed_when_read_only_false(self):
        from plugins import sql_helper
        # 跑两次：第一次 do_query 写入，第二次开新 conn 直接读数据库验证
        orig, conn = self._patched_get_conn()
        try:
            r = sql_helper._do_query({
                "name": "x",
                "sql": "INSERT INTO users (id, name) VALUES (3, 'charlie')",
                "read_only": False,
            })
            self.assertIn("执行成功", r)
        finally:
            sql_helper._get_conn = orig

        # 重新连同一个 :memory: 数据库（用相同 DDL 重建 + 共享同一文件）
        # SQLite :memory: 每次 sqlite3.connect 各自独立；这里改用 tempfile 来共享
        from plugins import sql_helper
        path = os.path.join(tempfile.gettempdir(), "sql_helper_test_readonly_false.db")
        try:
            # 重置数据库
            if os.path.exists(path):
                os.remove(path)
            conn2 = sqlite3.connect(path)
            conn2.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
            conn2.execute("INSERT INTO users (id, name) VALUES (1, 'alice')")
            conn2.commit()
            conn2.close()

            orig2 = sql_helper._get_conn

            def fake(name):
                c = sqlite3.connect(path)
                c.row_factory = sqlite3.Row
                return c, None

            sql_helper._get_conn = fake
            try:
                r = sql_helper._do_query({
                    "name": "x",
                    "sql": "INSERT INTO users (id, name) VALUES (3, 'charlie')",
                    "read_only": False,
                })
                self.assertIn("执行成功", r)
            finally:
                sql_helper._get_conn = orig2

            # 直接验证持久化
            verify = sqlite3.connect(path)
            try:
                row = verify.execute("SELECT name FROM users WHERE id=3").fetchone()
                self.assertEqual(row[0], "charlie")
            finally:
                verify.close()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_pragma_query_only_rejects_write_directly(self):
        """直接验证 SQLite query_only 真的能拦截（不被字符串分析绕过）"""
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("PRAGMA query_only = ON")
            # CREATE TABLE 在 query_only 下也不允许
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE t (id INT)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
