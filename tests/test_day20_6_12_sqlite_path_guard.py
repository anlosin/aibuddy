# -*- coding: utf-8 -*-
"""Day 20.6.12 护栏：SQLite 数据库路径必须约束在 workspace 内（P0-SEC-6）

来源：Day 20.6.10 审计 → Day 20.6.11 复核（audit_verification.md P0-SEC-6）。
事实：sql_helper._connect 里 `sqlite3.connect(cfg.get("path"))` 无任何路径约束，
      而 sqlite3 **对不存在的路径会直接创建文件** —— LLM 可用
      `db_connect(type=sqlite, path=<任意位置>)` 在工作区之外落地数据库文件。

修复：新增 _safe_sqlite_path，规则与 write_file._safe_path 对齐
      （`:memory:` 特例放行；相对路径归到 workspace 根；绝对路径必须在其中）。
      __init__ 侧越界直接返回错误串，不写连接配置、不创建文件。
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins import sql_helper, write_file  # noqa: E402

OUTSIDE = os.path.abspath(os.path.join(ROOT, os.pardir, "evil_guard.sqlite"))


def _outcome(fn, arg):
    try:
        return (True, fn(arg))
    except Exception as e:                # noqa: BLE001
        return (False, type(e).__name__)


class TestSqlitePathGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sqlguard_")
        self._conn_file = sql_helper.CONN_FILE
        self._cfg = sql_helper._CFG
        sql_helper.CONN_FILE = os.path.join(self.tmp, "db_connections.json")
        if os.path.exists(OUTSIDE):
            os.remove(OUTSIDE)

    def tearDown(self):
        sql_helper.CONN_FILE = self._conn_file
        sql_helper._CFG = self._cfg
        shutil.rmtree(self.tmp, ignore_errors=True)
        if os.path.exists(OUTSIDE):
            os.remove(OUTSIDE)

    # ── 纯函数层 ──
    def test_memory_special_case_allowed(self):
        self.assertEqual(sql_helper._safe_sqlite_path(":memory:"), ":memory:")
        self.assertEqual(sql_helper._safe_sqlite_path(""), ":memory:")

    def test_relative_resolves_inside_workspace(self):
        p = sql_helper._safe_sqlite_path("app.db")
        self.assertTrue(os.path.isabs(p), "应返回绝对路径")
        self.assertTrue(p.endswith("app.db"))
        from qwen_app.workspace import resolve_workspace
        root_real = os.path.realpath(resolve_workspace())
        self.assertTrue(os.path.realpath(p).startswith(root_real),
                        f"相对路径应落在 workspace 内: {p}")

    def test_absolute_outside_rejected(self):
        with self.assertRaises(ValueError):
            sql_helper._safe_sqlite_path(OUTSIDE)

    def test_traversal_rejected(self):
        for s in (os.path.join("..", os.pardir, "escape.db"),
                  "../escape.db",
                  "a/b/../../../../../../tmp/escape.db"):
            with self.subTest(sample=s):
                with self.assertRaises(ValueError):
                    sql_helper._safe_sqlite_path(s)

    def test_parity_with_write_file(self):
        """除 ':memory:' 特例外，与 write_file 的写路径约束行为一致"""
        for s in ("app.db", "sub/app.db", "../escape.db", OUTSIDE,
                  os.path.join(ROOT, "inside.db"),
                  "C:\\Windows\\Temp\\x.db", "/tmp/x.db"):
            with self.subTest(sample=s):
                self.assertEqual(
                    _outcome(sql_helper._safe_sqlite_path, s),
                    _outcome(write_file._safe_path, s),
                    f"与 write_file 行为不一致: {s!r}",
                )

    # ── 工具入口端到端 ──
    def test_db_connect_rejects_outside_path(self):
        r = sql_helper.execute("db_connect", {
            "name": "evil", "type": "sqlite", "path": OUTSIDE})
        self.assertIn("越界", r, f"未拒绝越界路径: {r}")
        self.assertFalse(os.path.exists(OUTSIDE), "越界数据库文件被创建了")
        self.assertFalse(os.path.exists(sql_helper.CONN_FILE),
                         "被拒绝的连接不应写入连接配置")

    def test_db_connect_memory_still_works(self):
        r = sql_helper.execute("db_connect", {
            "name": "mem", "type": "sqlite", "path": ":memory:"})
        self.assertIn("连接成功", r, f"内存库连接被误伤: {r}")

    def test_db_connect_relative_still_works(self):
        """工作区内的相对路径是正常用法，不能被误杀"""
        r = sql_helper.execute("db_connect", {
            "name": "rel", "type": "sqlite", "path": "guard_test.db"})
        self.assertIn("连接成功", r, f"相对路径被误伤: {r}")


if __name__ == "__main__":
    unittest.main()
