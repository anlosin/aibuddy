"""Day 18 字体兜底 + SQLite 锁测试 — M6 + M8。"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _IsolatedConfigDB(unittest.TestCase):
    """每个用例把 config 重定向到 tmpdir（Day 20.6.19：避免污染生产 db）。

    修前 test_config_threading.py 直接打生产 data/conversations.db，
    每次跑全量回归会写入 10 条 lock-test-{0..9} 到用户真实的对话列表。
    """

    def setUp(self):
        from qwen_app import config
        self._tmp = tempfile.mkdtemp(prefix="config_threading_")
        db = os.path.join(self._tmp, "conversations.db")
        config.set_db_path_for_tests(db)
        self._config = config

    def tearDown(self):
        from qwen_app import config
        try:
            config.close_all_conns()
        except Exception:
            pass
        config.set_db_path_for_tests(None)
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestStatusBarFontFallback(unittest.TestCase):
    """M6: 状态栏 QSS 必须有 CJK 字体降级链。"""

    def test_chat_window_statusbar_has_cjk_fallback(self):
        from qwen_app import chat_window
        with open(chat_window.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Microsoft YaHei", src,
                      "状态栏 QSS 必须含 CJK 字体兜底（Linux 无中文字体环境会渲染成 ?）")
        # 找 status_label.setStyleSheet 那行
        i = src.find("status_label.setStyleSheet")
        self.assertGreater(i, -1, "找不到 status_label.setStyleSheet 调用")
        block = src[i:i + 500]
        # 至少含 3 个不同字体（降级链）
        comma_count = block.count(",")
        self.assertGreaterEqual(comma_count, 3,
                                 "CJK 字体降级链至少 3 个候选（Microsoft YaHei, "
                                 "PingFang SC, Noto Sans CJK SC 等）")
        # 关键的兜底字体（无中文字体环境能 fall back 的）
        self.assertIn("Noto Sans CJK", block,
                      "必须含 Noto Sans CJK SC —— Linux 上最通用的 CJK 字体")


class TestSqliteConnLock(_IsolatedConfigDB):
    """M8: 写操作必须持 _CONN_LOCK，防止同线程内两路并发写踩坏。"""

    def test_concurrent_save_single_conversation_safe(self):
        config = self._config

        # 确保 DB 已建好（用 init）
        config.init_conversations_db()

        # 起 10 个线程同时调 save_single_conversation
        # 失败模式：sqlite3.OperationalError: database is locked
        errors = []
        lock = threading.Lock()
        N = 10

        def writer(idx):
            try:
                cid = f"lock-test-{idx}"
                # 第一次 INSERT
                config.save_single_conversation(
                    {"id": cid, "title": f"t{idx}", "history": [],
                     "created_at": "2026-09-12T00:00:00"},
                    cid,
                )
                # 第二次 UPDATE（同一主键）
                config.save_single_conversation(
                    {"id": cid, "title": f"t{idx}-v2", "history": [{"role": "user", "content": "x"}],
                     "created_at": "2026-09-12T00:00:00"},
                    cid,
                )
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(N)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)

        self.assertEqual(errors, [],
                         "并发 save_single_conversation 不应有 lock 冲突，"
                         "实际错误: %s" % errors[:3])

        # 验证全部成功写入
        convs, _ = config.load_conversations()
        for i in range(N):
            conv = next((c for c in convs if c["id"] == f"lock-test-{i}"), None)
            self.assertIsNotNone(conv, f"lock-test-{i} 应该被保存")
            self.assertEqual(conv["title"], f"t{i}-v2",
                             f"lock-test-{i} 应该是第二次写入的 v2 版本")

    def test_lock_is_module_level(self):
        """_CONN_LOCK 必须是模块级单例，跨函数共用一把锁"""
        config = self._config
        self.assertTrue(hasattr(config, "_CONN_LOCK"),
                        "config 模块必须有 _CONN_LOCK")
        # 必须是 threading.Lock 实例
        self.assertIsInstance(config._CONN_LOCK, type(threading.Lock()))


class TestConfigThreadConstraintDoc(unittest.TestCase):
    """M8: 文档必须说明"同进程内不能 GUI + scheduler_run 共启"约束。"""

    def test_doc_states_no_dual_process(self):
        from qwen_app import config
        with open(config.__file__, encoding="utf-8") as f:
            src = f.read()
        # 注释里必须提"不能同进程同启"或"SQLITE_BUSY"
        self.assertTrue(
            "SQLITE_BUSY" in src or "scheduler_run" in src,
            "config.py 必须有线程约束文档（SQLITE_BUSY 或 scheduler_run 风险说明）"
        )


if __name__ == "__main__":
    unittest.main()
