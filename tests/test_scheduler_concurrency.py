"""Day 18 scheduler 并发安全测试 — M3 (check_due 快照) + M8 (SQLite 线程说明)。"""
import os
import sys
import threading
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestCheckDueSnapshot(unittest.TestCase):
    """M3: check_due 必须在持锁内取 auto 快照，避免外部 delete_automation /
    update_automation 改字段时线程拿到过时数据。"""

    def setUp(self):
        from qwen_app.scheduler import Scheduler
        # 不实际执行，只验证元数据
        self.sched = Scheduler.__new__(Scheduler)
        self.sched._lock = threading.Lock()
        self.sched._running = set()
        self.sched.automations = []
        self.sched._finished_callbacks = []
        self.sched._client = None
        self.sched._model_id = "fake"
        self.sched._enable_thinking = False
        self.sched._enable_tools = False
        self.sched._plugins = []
        self.sched._enabled_plugins = []
        self.sched._max_rounds = 1

    def _make_due_auto(self, aid):
        import datetime
        return {
            "id": aid,
            "name": "task-" + aid,
            "prompt": "hello",
            "schedule": {"type": "interval", "unit": "minutes", "every": 1},
            "enabled": True,
            "last_run": None,
            "created_at": datetime.datetime.now().isoformat(),
        }

    def test_check_due_passes_deepcopy_to_thread(self):
        """线程拿到的 auto 与原始 automations 不再共享引用。"""
        self.sched.automations = [self._make_due_auto("aaa")]

        captured = {}
        import threading
        original_start = self.sched._run_one

        def fake_run(auto):
            captured["auto"] = auto
            captured["auto_id"] = id(auto)
        # monkey-patch _run_one 走单线程
        self.sched._run_one = fake_run

        # 启动 1 个"线程"，但我们用 Event 阻塞，在 check_due 期间改 automations
        gate = threading.Event()
        finished = threading.Event()

        def blocking_run(auto):
            captured["auto"] = auto
            captured["orig_id"] = id(auto)
            gate.set()
            finished.wait(timeout=1.0)

        self.sched._run_one = blocking_run

        # 启动 check_due（拿快照 → 起线程）
        self.sched.check_due()
        gate.wait(1.0)
        self.assertTrue(gate.is_set(), "_run_one 应已被调用")

        # 现在改原 automations 列表 —— 线程拿到的 deep copy 不应受影响
        self.sched.automations[0]["prompt"] = "MUTATED"
        finished.set()

        # 验证：线程捕获的 auto prompt 仍是原始值
        self.assertEqual(captured["auto"]["prompt"], "hello",
                         "M3：check_due 必须传 deep copy，否则外部 update 后线程内会读到错值")

    def test_check_due_dedup_under_lock(self):
        """同一任务在 check_due 内不会被重复加入 _running。"""
        a = self._make_due_auto("dup")
        self.sched.automations = [a, a]  # 假设重复（边界情况）

        called = []

        def fake_run(auto):
            called.append(auto["id"])
        self.sched._run_one = fake_run

        self.sched.check_due()
        # 等线程启动
        time.sleep(0.1)
        self.assertEqual(len(called), 1, "重复 aid 应被去重")


if __name__ == "__main__":
    unittest.main()
