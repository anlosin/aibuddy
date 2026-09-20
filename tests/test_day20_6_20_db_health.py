# -*- coding: utf-8 -*-
"""Day 20.6.20 护栏：启动期数据库健康检查（integrity_check）。

背景（Day 20.6.19 事故）：生产 db 曾被 truncate 至 0 字节 + schema 丢失，
save_conversations 的 try/except 静默吞掉一切错误，损坏全程无告警。

本护栏验证 _check_db_health 的三种处置：
1. 健康 db → 原样放行，不改名
2. 0 字节文件（被 truncate）→ 判损坏：改名 .corrupt-<ts> 保留现场 + 重建
3. 非 SQLite 格式垃圾文件 → quick_check 抛 DatabaseError → 判损坏同上
4. 每进程只检查一次（_DB_HEALTH_CHECKED 标记）；切库（set_db_path_for_tests）重置

全部落在 tmpdir，不碰生产 db。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DbHealthTests(unittest.TestCase):
    def setUp(self):
        from qwen_app import config
        self.cfg = config
        self.tmp = tempfile.mkdtemp(prefix="db_health_")
        self.db = os.path.join(self.tmp, "conversations.db")
        config.set_db_path_for_tests(self.db)

    def tearDown(self):
        try:
            self.cfg.close_all_conns()
        except Exception:
            pass
        self.cfg.set_db_path_for_tests(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _corrupt_files(self):
        """tmpdir 里 .corrupt- 保留现场文件列表"""
        return [f for f in os.listdir(self.tmp) if ".corrupt-" in f]

    def test_healthy_db_passes_unchanged(self):
        """健康库：建表写数据后重连，不应触发改名"""
        self.cfg.init_conversations_db()
        self.cfg.save_single_conversation(
            {"id": "h1", "title": "healthy", "history": [],
             "created_at": "2026-09-20T00:00:00"},
            "h1",
        )
        # 重置标记 + 关连接，模拟「进程重启后首次连接」
        self.cfg.close_all_conns()
        self.cfg._reset_db_health_for_tests()
        convs, _ = self.cfg.load_conversations()
        self.assertEqual(len(convs), 1)
        self.assertEqual(self._corrupt_files(), [], "健康库绝不能被改名")

    def test_truncated_zero_byte_file_is_quarantined(self):
        """0 字节文件（truncate 事故形态）→ 保留现场 + 重建"""
        # 先造一个健康库
        self.cfg.init_conversations_db()
        self.cfg.save_single_conversation(
            {"id": "a", "title": "a", "history": [], "created_at": "x"}, "a")
        self.cfg.close_all_conns()
        # 模拟 truncate 事故
        with open(self.db, "wb"):
            pass
        self.assertEqual(os.path.getsize(self.db), 0)
        self.cfg._reset_db_health_for_tests()

        # 重新连接触发健康检查
        convs, _ = self.cfg.load_conversations()
        # 1) 现场已保留
        corrupt = self._corrupt_files()
        self.assertTrue(any(f.startswith("conversations.db.corrupt-") for f in corrupt),
                        f"0 字节文件必须被改名保留现场，实际: {corrupt}")
        # 2) 全新库可用（schema 重建、列表为空）
        self.assertEqual(convs, [])

    def test_garbage_file_is_quarantined(self):
        """非 SQLite 格式垃圾 → quick_check 抛 DatabaseError → 判损坏"""
        with open(self.db, "wb") as f:
            f.write(b"this is definitely not a sqlite database" * 100)
        self.cfg._reset_db_health_for_tests()

        convs, _ = self.cfg.load_conversations()
        corrupt = self._corrupt_files()
        self.assertTrue(any(f.startswith("conversations.db.corrupt-") for f in corrupt),
                        f"垃圾文件必须被改名保留现场，实际: {corrupt}")
        self.assertEqual(convs, [])
        # 重建后的库可正常写入
        self.cfg.save_single_conversation(
            {"id": "b", "title": "b", "history": [], "created_at": "x"}, "b")
        convs, _ = self.cfg.load_conversations()
        self.assertEqual([c["id"] for c in convs], ["b"])

    def test_check_runs_once_per_db(self):
        """同一库多次连接只检查一次（_DB_HEALTH_CHECKED 标记）；
        标记置位后文件中途损坏 → 不再体检，错误如实抛出（不静默吞）"""
        self.cfg.init_conversations_db()
        self.cfg.close_all_conns()
        self.cfg._reset_db_health_for_tests()
        self.cfg.load_conversations()   # 第一次连接：检查跑掉
        self.assertTrue(self.cfg._DB_HEALTH_CHECKED)
        self.cfg.close_all_conns()
        # 把文件换成垃圾——标记已置位，不应再触发 quarantine（运行中性能约束）
        with open(self.db, "wb") as f:
            f.write(b"garbage" * 50)
        with self.assertRaises(sqlite3.DatabaseError):
            self.cfg.load_conversations()
        self.assertEqual(self._corrupt_files(), [], "标记已置位就不应重复检查")


if __name__ == "__main__":
    unittest.main()
