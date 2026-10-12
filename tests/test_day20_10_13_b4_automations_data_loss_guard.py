# -*- coding: utf-8 -*-
"""Day 20.10.13 B4 数据丢失检测护栏：automations.json 损坏 / 缺失 / 空 三态可区分。

背景（用户报告：「任务全部消失了」）
========================================================================
- 用户答：「磁盘文件被外部清空，与代码无关；修代码也救不回数据」
- 18 分钟根因分析时间过长（数据丢失 in/out 路径没分类记录）
- 当前生产 scheduler.load_automations() 在 3 种情况下都静默返回 []：

    1. 文件不存在        → []  (OK: 首次启动 / 外部 rm 都正常)
    2. 文件存在 = `[]`   → []  (OK: 真没任务)
    3. 文件存在但 JSON 损坏 / 不是 list → []  (BAD: 静默吞错 -> 任务丢失)

- 用户期望：B4 护栏能在「任务消失」时立刻区分是 disk 问题（数据文件状态）
  还是 code 问题（scheduler 加载路径），避免再花 18 分钟做根因分析。

本测试钉死的行为：

  T1. `load_automations()` 永远返回 list（绝不抛异常，绝不返 None）—— 防
      AutomationManagerDialog 后续 `.automations` 属性触 AttributeError
  T2. 三态可区分：
       - 文件不存在 → []
       - 文件存在 = `[]` → []
       - 文件损坏 → []（不抛）+ 记录 ERROR 日志 + 留一份 .corrupted-* 备份
       - 文件非 list（如 dict / str）→ [] + 记录 WARNING
  T3. Scheduler() 在数据为空 (automations.json = []) 时仍能正常构造
      + QTimer 不崩 + check_due() 不崩
  T4. 未来如果再次出现「任务消失」，scheduler.py 数据加载路径日志里能找到
      「automations.json 解析失败」或「顶层不是 list」的明确线索

文件位置覆盖：把 scheduler.AUTOMATIONS_FILE 临时指向临时文件，避免污染真实 data/
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen_app import scheduler as sched_mod
from qwen_app.scheduler import (
    load_automations, AUTOMATIONS_FILE, Scheduler,
)


# ═════════════════════════════════════════════════════════════════════
#  Fixture helpers
# ═════════════════════════════════════════════════════════════════════

def _isolated_automations_file(tmpdir, content=None, exists=True):
    """构造一个隔离的 automations.json 文件用于测试。

    content: 要写入的内容（None = 不写，模拟文件缺失）
    exists: False = 完全不创建文件
    """
    target = os.path.join(str(tmpdir), "automations.json")
    if not exists:
        # 保险起见，先删
        if os.path.exists(target):
            os.remove(target)
        return target
    if content is None:
        # 期望 exists=True 但 content=None 调用方主动给值；这里做对空文件兜底
        with open(target, "w", encoding="utf-8") as f:
            pass
        return target
    with open(target, "w", encoding="utf-8") as f:
        if isinstance(content, (str, bytes)):
            f.write(content)
        else:
            json.dump(content, f, ensure_ascii=False)
    return target


# ═════════════════════════════════════════════════════════════════════
#  T1：load_automations 永远返回 list，绝不返 None / 抛异常
# ═════════════════════════════════════════════════════════════════════

class TestLoadAutomationsAlwaysReturnsList(unittest.TestCase):
    """defensive: 不论文件状态如何，load_automations() 必返 []，UI 不要担心 None."""

    def test_returns_list_when_file_missing(self):
        """文件不存在 → []"""
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE",
                                   os.path.join(td, "automations.json")):
                # 文件不存在
                self.assertFalse(os.path.exists(sched_mod.AUTOMATIONS_FILE))
                result = load_automations()
                self.assertIsInstance(result, list,
                                      "load_automations 在文件缺失时必须返 list")
                self.assertEqual(result, [])

    def test_returns_empty_list_not_none_for_corrupted_file(self):
        """文件存在但 JSON 损坏 → []（不抛 / 不返 None）"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content="{ this is not json [")
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                result = load_automations()
                self.assertIsNotNone(result,
                                     "load_automations 在损坏文件下不能返 None")
                self.assertIsInstance(result, list)
                self.assertEqual(result, [],
                                 "损坏文件 → 必须返 [] 让 UI 还能弹『暂无任务』")

    def test_returns_empty_list_when_top_level_not_list(self):
        """文件存在但顶层是 dict → []"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content={"oops": "not a list"})
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                result = load_automations()
                self.assertIsInstance(result, list)
                self.assertEqual(result, [])

    def test_never_raises_on_any_disk_state(self):
        """边界：load_automations 对任何文件系统状态都不抛异常。
        跑 5 种状态确认 0 异常。"""
        cases = [
            ("missing", _isolated_automations_file("/no/such/dir", exists=False), False),
            ("empty_list", _isolated_automations_file(tempfile.mkdtemp(), content=[]), True),
            ("empty_file", _isolated_automations_file(tempfile.mkdtemp(), content=""), True),
            ("corrupt", _isolated_automations_file(
                tempfile.mkdtemp(), content="NOT JSON AT ALL \\xFF\\xFE"), True),
            ("dict_top", _isolated_automations_file(
                tempfile.mkdtemp(), content={"x": 1}), True),
            ("str_top", _isolated_automations_file(
                tempfile.mkdtemp(), content="just a string"), True),
            ("null_top", _isolated_automations_file(
                tempfile.mkdtemp(), content=None), True),
        ]
        for label, path, exists_ok in cases:
            with self.subTest(case=label):
                with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", path):
                    try:
                        result = load_automations()
                        self.assertIsInstance(result, list,
                                             f"[{label}] load_automations 必须返 list；实际: {type(result).__name__}")
                    except Exception as e:
                        self.fail(f"[{label}] load_automations 抛异常: {type(e).__name__}: {e}")
                # cleanup the temporary dir (path's parent)
                parent_dir = os.path.dirname(path) if exists_ok and os.path.exists(path) else None
                if parent_dir and os.path.exists(parent_dir) and "tmp" in parent_dir:
                    try:
                        shutil.rmtree(parent_dir, ignore_errors=True)
                    except Exception:
                        pass


# ═════════════════════════════════════════════════════════════════════
#  T2：三态可区分 —— 关键！文件缺失 vs 空数组 vs 损坏文件，必须在日志留痕
# ═════════════════════════════════════════════════════════════════════

class TestDiskStateTriClassification(unittest.TestCase):
    """三类状态在日志里有不同标记：

    - 文件不存在 → silent（首次启动正常情况，warn 会污染日志）
    - 文件 = [] → silent（合法空任务）
    - 文件损坏 → ERROR + .corrupted-<ts> 备份
    - 文件不是 list → WARNING

    这样未来如果「任务消失」，只看 ERROR/WARN 行就能知道是 disk 被外清（情况1）
    还是 JSON 损坏（情况3），还是 schema 不对（情况4）。
    """

    def _capture_logs(self):
        """用 StringIO 抓 logger 日志。"""
        buf = logging.handlers.MemoryHandler(capacity=1000) if False else None
        # 简单做法：捕获所有 handler 写入到 list
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        cap = _Capture(logging.DEBUG)
        logger_name = "qwen_app.scheduler"
        target_logger = logging.getLogger(logger_name)
        target_logger.addHandler(cap)
        return records, target_logger, cap

    def test_corrupt_file_logs_error_and_backs_up(self):
        """JSON 损坏 → logger.error + .corrupted-<ts> 备份"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content="this is not json { ]")
            # 设为 cron timestamps 不同 → 多次跑不撞名
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                records, target_logger, cap = self._capture_logs()
                try:
                    result = load_automations()
                finally:
                    target_logger.removeHandler(cap)
                # 1) 返 []
                self.assertEqual(result, [])
                # 2) ERROR 日志包含「automations.json 解析失败」
                error_msgs = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
                self.assertTrue(
                    any("automations.json 解析失败" in m for m in error_msgs),
                    f"损坏文件必须 ERROR 日志；实际 records={[r.getMessage() for r in records]}",
                )
                # 3) 备份文件存在
                backups = [f for f in os.listdir(td)
                           if f.startswith("automations.json.corrupted-")]
                self.assertTrue(
                    len(backups) >= 1,
                    f"损坏文件必须留 .corrupted-<ts> 备份；实际 ls={os.listdir(td)}",
                )

    def test_empty_list_does_not_warn_or_error(self):
        """文件存在 = [] （合法空任务）→ 不应有 ERROR/WARN 日志污染"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content=[])
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                records, target_logger, cap = self._capture_logs()
                try:
                    result = load_automations()
                finally:
                    target_logger.removeHandler(cap)
                self.assertEqual(result, [])
                error_msgs = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
                warn_msgs = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
                self.assertEqual(
                    error_msgs, [],
                    f"空列表不应有 ERROR 日志；实际={error_msgs}",
                )
                self.assertEqual(
                    warn_msgs, [],
                    f"空列表不应有 WARN 日志；实际={warn_msgs}",
                )

    def test_missing_file_does_not_warn_or_error(self):
        """文件不存在 → silent（不产生 ERROR/WARN）"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "automations.json")
            self.assertFalse(os.path.exists(p))
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                records, target_logger, cap = self._capture_logs()
                try:
                    result = load_automations()
                finally:
                    target_logger.removeHandler(cap)
                self.assertEqual(result, [])
                error_msgs = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
                warn_msgs = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
                self.assertEqual(
                    error_msgs, [],
                    f"文件缺失不应有 ERROR 日志；实际={error_msgs}",
                )
                self.assertEqual(
                    warn_msgs, [],
                    f"文件缺失不应有 WARN 日志；实际={warn_msgs}",
                )

    def test_non_list_top_level_logs_warning(self):
        """顶层是 dict → WARNING 日志 + 返 []"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content={"key": "value"})
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                records, target_logger, cap = self._capture_logs()
                try:
                    result = load_automations()
                finally:
                    target_logger.removeHandler(cap)
                self.assertEqual(result, [])
                warn_msgs = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
                self.assertTrue(
                    any("顶层不是 list" in m for m in warn_msgs),
                    f"非 list 顶层应 WARN；实际={[r.getMessage() for r in records]}",
                )


# ═════════════════════════════════════════════════════════════════════
#  T3：Scheduler 在数据为空时仍能正常构造，QTimer 不崩
# ═════════════════════════════════════════════════════════════════════

class TestSchedulerOkWithEmptyData(unittest.TestCase):
    """Scheduler 必须能在 automations.json = [] / 文件不存在 时正常构造。

    防「任务消失」故事重演：
    - 用户外部 rm 了文件
    - Scheduler 启动时 load_automations() 返 []
    - 不能因 .automations 是空就 crash（_record / check_due 等必须容忍 []）
    """

    def test_scheduler_constructs_with_zero_tasks(self):
        """文件缺失 → Scheduler 仍正常构造 .automations = []"""
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "automations.json")
            self.assertFalse(os.path.exists(missing))
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", missing), \
                 mock.patch.object(sched_mod, "load_runs_index", return_value=[]), \
                 mock.patch.object(sched_mod, "save_runs_index", lambda idx: None), \
                 mock.patch.object(sched_mod, "save_automations", lambda autos: None):
                sch = Scheduler(client=None, model_id="mock-qwen",
                                plugins=[], enabled_plugins=[])
                try:
                    self.assertEqual(sch.automations, [],
                                     "Scheduler 构造后 .automations 必为 []")
                    self.assertIsInstance(sch.automations, list)
                    # check_due 在空数据上必须不抛
                    sch.check_due()  # 应该 no-op 不抛
                finally:
                    pass

    def test_scheduler_constructs_with_corrupt_file(self):
        """文件损坏 → Scheduler 构造不抛，.automations = []"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content="<<broken json>>")
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p), \
                 mock.patch.object(sched_mod, "load_runs_index", return_value=[]), \
                 mock.patch.object(sched_mod, "save_runs_index", lambda idx: None), \
                 mock.patch.object(sched_mod, "save_automations", lambda autos: None):
                sch = Scheduler(client=None, model_id="mock-qwen",
                                plugins=[], enabled_plugins=[])
                try:
                    self.assertEqual(sch.automations, [],
                                     "损坏文件 → Scheduler.automations 必为 []")
                    self.assertIsInstance(sch.automations, list)
                    sch.check_due()
                finally:
                    pass

    def test_qtimer_starts_with_empty_automations(self):
        """空数据 + QTimer.start() 不抛"""
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "automations.json")
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", missing), \
                 mock.patch.object(sched_mod, "load_runs_index", return_value=[]), \
                 mock.patch.object(sched_mod, "save_runs_index", lambda idx: None), \
                 mock.patch.object(sched_mod, "save_automations", lambda autos: None):
                sch = Scheduler(client=None, model_id="mock-qwen",
                                plugins=[], enabled_plugins=[])
                try:
                    # 用 real QTimer 在 offscreen 模式测
                    from PyQt5.QtCore import QTimer
                    from PyQt5.QtWidgets import QApplication
                    app = QApplication.instance() or QApplication(sys.argv)
                    timer = QTimer()
                    timer.setInterval(30000)
                    timer.timeout.connect(sch.check_due)
                    timer.start()  # 不抛即说明容错 OK
                    self.assertTrue(timer.isActive())
                    timer.stop()
                finally:
                    pass

    def test_scheduler_attributes_accessible_when_empty(self):
        """空任务时 Scheduler 暴露的所有对外属性都不抛 AttributeError"""
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "automations.json")
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", missing), \
                 mock.patch.object(sched_mod, "load_runs_index", return_value=[]), \
                 mock.patch.object(sched_mod, "save_runs_index", lambda idx: None), \
                 mock.patch.object(sched_mod, "save_automations", lambda autos: None):
                sch = Scheduler(client=None, model_id="mock-qwen",
                                plugins=[], enabled_plugins=[])
                # AutomationManagerDialog 常用属性都试一遍
                for attr in ("automations", "on_log", "_running", "_lock",
                             "client", "model_id", "plugins", "enabled_plugins",
                             "max_rounds", "_finished_callbacks"):
                    try:
                        getattr(sch, attr)
                    except AttributeError as e:
                        self.fail(f"空任务时 sch.{attr} 不应抛 AttributeError: {e}")


# ═════════════════════════════════════════════════════════════════════
#  T4：根因分析可追溯 —— 加一个新函数返回 (loaded_data, on_disk_state)
#  或至少确保 load_automations() 在损坏时的 ERROR 日志包含足够信息
# ═════════════════════════════════════════════════════════════════════

class TestLoadAutomationsDiagnosticInfo(unittest.TestCase):
    """未来根因分析必备：load_automations() 在损坏时给详细上下文。"""

    def test_corruption_error_includes_file_path(self):
        """损坏文件 → ERROR 日志必须带文件路径，方便定位 disk 问题"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content="[broken")
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                records = []
                class _Cap(logging.Handler):
                    def emit(self, rec):
                        records.append(rec)
                cap = _Cap(logging.DEBUG)
                logger_name = "qwen_app.scheduler"
                target_logger = logging.getLogger(logger_name)
                target_logger.addHandler(cap)
                try:
                    load_automations()
                finally:
                    target_logger.removeHandler(cap)

                error_msgs = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
                # 至少有一条 ERROR 提到文件路径
                self.assertTrue(
                    any(p in m or "automations.json" in m for m in error_msgs),
                    f"损坏 ERROR 日志应含文件路径/文件名；实际 msgs={error_msgs}",
                )

    def test_backup_path_recorded_in_logs(self):
        """损坏 → ERROR 日志应含 .corrupted-<ts> 备份路径"""
        with tempfile.TemporaryDirectory() as td:
            p = _isolated_automations_file(td, content="<bad json>")
            with mock.patch.object(sched_mod, "AUTOMATIONS_FILE", p):
                records = []
                class _Cap(logging.Handler):
                    def emit(self, rec):
                        records.append(rec)
                cap = _Cap(logging.DEBUG)
                logger = logging.getLogger("qwen_app.scheduler")
                logger.addHandler(cap)
                try:
                    load_automations()
                finally:
                    logger.removeHandler(cap)

                all_msgs = " ".join(r.getMessage() for r in records)
                self.assertIn(".corrupted-", all_msgs,
                              f"损坏日志必须含备份路径；实际 msgs={[r.getMessage() for r in records]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
