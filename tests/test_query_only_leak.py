"""Day 19 隐藏 bug：sql_helper PRAGMA query_only 状态泄漏到后续 write。

Day 18 C2 修复：read_only=True 时设 PRAGMA query_only=ON。
但 PRAGMA 是连接级状态，函数返回后 query_only 仍 ON。
下次 _do_query(read_only=False) 时同连接仍处于只读模式，
SQLite 拒绝 INSERT/UPDATE → silent failure。

严重性：高 —— LLM 通过 db_query 写入数据会静默失败，
插件作者排查时发现 INSERT 没生效，但没异常（SQLite 静默拒绝）。

Day 19.1.1 修复：_do_query 每次显式重置 query_only 状态
（read_only=True → ON，read_only=False → OFF），
连接级状态泄漏不再影响后续调用。
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
        （PRAGMA 是连接级状态，sqlite3.connect 同一 conn 仍生效）

        此测试是文档性测试：证明 SQLite PRAGMA query_only 确实是
        连接级状态，不主动重置就会泄漏。
        """
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

    def test_sql_helper_read_only_false_resets_query_only(self):
        """Day 19.1.1: 即使第一次 read_only=True 把 query_only=ON，
        第二次 read_only=False 必须显式 PRAGMA query_only=OFF，
        INSERT 不再静默失败。

        这是 query_only 泄漏修复的回归测试：
        修复前：第二次 INSERT 报 readonly 错误或静默无变化。
        修复后：第二次 INSERT 真的把数据写进去，第三次 SELECT 能看到。
        """
        from plugins import sql_helper
        from qwen_app.workspace import set_active_workspace, clear_active_workspace
        # Day 20.6.12 (P0-SEC-6)：sqlite 路径被约束在 workspace 内。本测试关注
        # query_only 状态泄漏，与路径策略无关；把临时目录设为「活跃工作目录」，
        # 使 tmpdir 下的 db 合法（不依赖固定 workspace 位置，保持隔离与可清理）。
        tmpdir = tempfile.mkdtemp()
        set_active_workspace(tmpdir)
        db_path = os.path.join(tmpdir, "test.db")
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            conn.close()

            # 注册连接
            sql_helper.execute("db_connect", {
                "type": "sqlite",
                "path": db_path,
                "name": "test_db_qol",
            })

            # 第一次 read_only=True → 设 query_only=ON
            r1 = sql_helper.execute("db_query", {
                "name": "test_db_qol",
                "sql": "SELECT * FROM t",
                "read_only": True,
            })
            self.assertIn("1", r1, f"首次 SELECT 应见 1，实际：{r1!r}")

            # 第二次 read_only=False → 必须显式 PRAGMA query_only=OFF
            # 修复前：会报 "attempt to write a readonly database" 或静默无变化
            # 修复后：INSERT 真生效
            r2 = sql_helper.execute("db_query", {
                "name": "test_db_qol",
                "sql": "INSERT INTO t VALUES (2)",
                "read_only": False,
            })
            self.assertIn("影响行数", r2,
                          f"INSERT 应成功（'影响行数: 1'），实际：{r2!r}"
                          f"\n这说明 query_only=ON 状态泄漏到了 write 调用。")

            # 第三次 read_only=True → 应能见到 1 和 2
            r3 = sql_helper.execute("db_query", {
                "name": "test_db_qol",
                "sql": "SELECT * FROM t ORDER BY x",
                "read_only": True,
            })
            self.assertIn("1", r3, f"SELECT 应见 1，实际：{r3!r}")
            self.assertIn("2", r3,
                          f"SELECT 应见刚 INSERT 的 2（修复前会丢），实际：{r3!r}")

        finally:
            # 清理：关闭缓存连接 + 还原活跃工作目录 + 删 tmpdir
            try:
                cn, _ = sql_helper._get_conn("test_db_qol")
                if cn:
                    cn.close()
            except Exception:
                pass
            clear_active_workspace()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_sql_helper_alternating_read_only_mode(self):
        """Day 19.1.1: 反复切换 read_only=True/False 不应出现状态污染。"""
        from plugins import sql_helper
        from qwen_app.workspace import set_active_workspace, clear_active_workspace
        # Day 20.6.12 (P0-SEC-6)：同 test_sql_helper_read_only_false_resets_query_only，
        # 把临时目录设为活跃工作目录，使 db 路径满足 workspace 约束。
        tmpdir = tempfile.mkdtemp()
        set_active_workspace(tmpdir)
        db_path = os.path.join(tmpdir, "test.db")
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
            conn.close()

            sql_helper.execute("db_connect", {
                "type": "sqlite",
                "path": db_path,
                "name": "test_db_alt",
            })

            # 交替 10 次
            for i in range(10):
                # read 一次
                r_r = sql_helper.execute("db_query", {
                    "name": "test_db_alt",
                    "sql": "SELECT count(*) FROM t",
                    "read_only": True,
                })
                self.assertNotIn("失败", r_r, f"第 {i} 次 SELECT 失败：{r_r!r}")

                # write 一次
                r_w = sql_helper.execute("db_query", {
                    "name": "test_db_alt",
                    "sql": f"INSERT INTO t VALUES ({i})",
                    "read_only": False,
                })
                self.assertIn("影响行数", r_w,
                              f"第 {i} 次 INSERT 失败：{r_w!r}"
                              f"\nquery_only 状态可能再次泄漏。")

            # 验证最终 10 行都写进去了
            r_check = sql_helper.execute("db_query", {
                "name": "test_db_alt",
                "sql": "SELECT count(*) FROM t",
                "read_only": True,
            })
            self.assertIn("10", r_check, f"应累计 10 行，实际：{r_check!r}")

        finally:
            try:
                cn, _ = sql_helper._get_conn("test_db_alt")
                if cn:
                    cn.close()
            except Exception:
                pass
            clear_active_workspace()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
