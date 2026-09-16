# -*- coding: utf-8 -*-
"""Day 20.6.6 护栏：自动化任务重复执行 bug（deepcopy last_run 丢失）

2026-09-15 晚事故：check_due 传 deepcopy 给 _run_one，_record 更新副本的
last_run 后丢弃，原 automations 不变 → is_due 每 30s 判定"到期"，
weather 任务 21:30~24:00 被无限重复执行 300+ 次。

三道护栏：
1. _record 同步回 self.automations 原对象（deepcopy 场景）
2. is_due 语义：daily 任务当晚执行后，当天不再触发、次日到点才触发
3. _strip_think 剥离思考标签（成对 / 未闭合 / thinking 变体）
"""
import copy
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen_app import scheduler as sched_mod
from qwen_app.scheduler import Scheduler, is_due, _strip_think


def _mk_scheduler(tmpdir):
    """构造 Scheduler 并把所有落盘操作指到临时目录，避免污染真实 data/"""
    with mock.patch.object(sched_mod, "load_automations", return_value=[]):
        sch = Scheduler(client=None, model_id="mock-qwen",
                        plugins=[], enabled_plugins=[])
    sch.automations = [{
        "id": "t1", "name": "x", "enabled": True,
        "schedule": {"type": "daily", "time": "20:30"},
        "last_run": None,
    }]
    p1 = mock.patch.object(sched_mod, "LOG_DIR", str(tmpdir))
    p2 = mock.patch.object(sched_mod, "load_runs_index", return_value=[])
    p3 = mock.patch.object(sched_mod, "save_runs_index", lambda idx: None)
    p4 = mock.patch.object(sched_mod, "save_automations", lambda autos: None)
    for p in (p1, p2, p3, p4):
        p.start()
    return sch, (p1, p2, p3, p4)


class TestRecordSync(unittest.TestCase):
    def test_record_syncs_back_to_original(self):
        """deepcopy 执行后 last_run 必须同步回 self.automations 原对象"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sch, patches = _mk_scheduler(td)
            try:
                snap = copy.deepcopy(sch.automations[0])   # 模拟 check_due 的 deepcopy
                started = datetime.datetime(2026, 9, 15, 21, 30, 17)
                sch._record(snap, started, datetime.datetime(2026, 9, 15, 21, 30, 38),
                            "ok", "final", [], "", "m")
                self.assertEqual(sch.automations[0]["last_run"],
                                 "2026-09-15T21:30:17")
                self.assertEqual(sch.automations[0]["last_status"], "ok")
            finally:
                for p in patches:
                    p.stop()

    def test_record_run_now_same_object_no_double_write(self):
        """run_now 传原对象本身（orig is auto）时不重复写也不报错"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sch, patches = _mk_scheduler(td)
            try:
                orig = sch.automations[0]
                started = datetime.datetime(2026, 9, 16, 8, 0, 0)
                sch._record(orig, started, datetime.datetime(2026, 9, 16, 8, 0, 20),
                            "ok", "final", [], "", "m")
                self.assertEqual(orig["last_run"], "2026-09-16T08:00:00")
            finally:
                for p in patches:
                    p.stop()


class TestIsDueDaily(unittest.TestCase):
    AUTO = {"id": "t1", "enabled": True,
            "schedule": {"type": "daily", "time": "20:30"}}

    def test_same_evening_not_due_again(self):
        """当晚执行过后（哪怕过了 20:30），当天剩余时间不再触发"""
        a = dict(self.AUTO, last_run="2026-09-15T21:30:17")
        for hm in [(21, 31), (22, 0), (23, 59)]:
            now = datetime.datetime(2026, 9, 15, *hm)
            self.assertFalse(is_due(a, now), f"{now} 不应触发")

    def test_next_day_trigger(self):
        """次日到点才触发；到点前不触发"""
        a = dict(self.AUTO, last_run="2026-09-15T21:30:17")
        self.assertFalse(is_due(
            a, datetime.datetime(2026, 9, 16, 20, 29, 59)))
        self.assertTrue(is_due(
            a, datetime.datetime(2026, 9, 16, 20, 30, 1)))


class TestStripThink(unittest.TestCase):
    def test_paired_block_removed(self):
        self.assertEqual(_strip_think("<think>思考</think>正文"),
                         "正文")

    def test_unclosed_dropped(self):
        self.assertEqual(_strip_think("<think>思考到一半"),
                         "")

    def test_thinking_variant(self):
        self.assertEqual(_strip_think("<thinking>x</thinking>\n答案"),
                         "答案")

    def test_no_tag_passthrough(self):
        self.assertEqual(_strip_think("普通回复"), "普通回复")

    def test_multiline(self):
        text = "<think>\n多行\n思考\n</think>\n\n**答案**"
        self.assertEqual(_strip_think(text), "**答案**")


if __name__ == "__main__":
    unittest.main()
