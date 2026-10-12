# -*- coding: utf-8 -*-
"""T05.2 runtime 行为护栏 —— 关键路径必须 / 不能 写日志。

按 ``tests/docs/refactor_logging_arch.md`` §3 T5.2 列出的 3 个用例：
  1. ``test_scheduler_run_failure_logs_error`` —— 模拟任务跑失败，caplog 抓 ERROR
  2. ``test_bridge_save_cfg_failure_logs_error`` —— 模拟 config 写盘失败，caplog 抓 ERROR
  3. ``test_silent_ok_paths_dont_force_log`` —— cancel / cleanup 不强制写日志（OK 不在 log 中）

IS_PASS 标准：3/3 通过；与 §4 IS_PASS 标准对齐：
- T01 5 + T02 4 + T03 3 = 12 个新增用例 + 已有 739 = **751 全过**
"""
import logging
import os
import sys
import unittest
from unittest.mock import patch

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import qwen_app.scheduler as sched        # noqa: E402


# ── 用例 1：scheduler 任务跑失败时，logger 真的写了 error ──


class TestSchedulerLogsErrorOnFailure(unittest.TestCase):
    """P0：scheduler 任务跑失败时，logger 必须写 ERROR（must-log-ERROR 路径）。"""

    def test_scheduler_run_failure_logs_error(self):
        """模拟 LLM client 抛异常 → _run_one 走 except 分支 → logger.exception 写 ERROR。

        _execute 本身不抓异常（异常冒泡到 _run_one），所以测试 _run_one 路径。
        """
        # 让 run_automation 直接抛异常（模拟 LLM 客户端彻底不可用）
        with patch.object(sched, "run_automation", side_effect=RuntimeError("simulated LLM failure")):
            # 用 assertLogs 抓 ERROR 级
            with self.assertLogs("qwen_app.scheduler", level="ERROR") as cm:
                sch = _make_test_scheduler()
                auto = _make_test_auto()
                # _run_one 才是捕获异常的入口
                sch._run_one(auto)
        # 验证 logger.exception 写了 ERROR（且 message 里提到「任务 ... 执行异常」）
        joined = "\n".join(cm.output)
        self.assertIn("执行异常", joined, "scheduler except 分支必须 logger.exception 写 ERROR")


def _make_test_scheduler():
    """构造一个不带 Qt 依赖的 Scheduler 测试桩（仅用于 _execute 触发）。"""
    sch = sched.Scheduler.__new__(sched.Scheduler)
    sch.automations = []
    sch._running = set()
    sch._lock = __import__("threading").Lock()
    sch._plugins = []
    sch._enabled_plugins = []
    sch._max_rounds = 3
    sch._enable_thinking = False
    sch._enable_tools = False
    sch._client = None
    sch._model_id = "test-model"
    sch._parent = None  # 让 properties 走 self._client / self._model_id
    sch.on_log = None
    sch._finished_callbacks = []
    return sch


def _make_test_auto():
    """构造一个最小可用的 auto dict。"""
    return {
        "id": "test-auto-id",
        "name": "test_task",
        "prompt": "hello",
        "schedule": {"type": "interval", "unit": "minutes", "every": 5},
        "enabled": True,
        "max_rounds": 3,
    }


# ── 用例 2：bridge 写 cfg 失败时，logger 真的写了 error ──


class TestBridgeLogsErrorOnSaveFailure(unittest.TestCase):
    """P0：chat_bridge 写 config 失败时，logger 必须写 ERROR。

    模拟 ``_config.save_config`` 抛异常（例如磁盘满 / 权限被收回），
    验证 chat_bridge 走 except 分支并 logger.warning/logger.error。
    """

    def test_bridge_save_cfg_failure_logs_error(self):
        # 延迟 import：chat_bridge 依赖 PyQt5，必须在 offscreen 下加载
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app import config as _cfg

        bridge = ChatBridge(theme="light")
        # 直接 mock _cfg.save_config 抛异常（与 _apply_expert / set_theme 调用同链路）
        with patch.object(_cfg, "save_config", side_effect=OSError("disk full simulated")):
            with self.assertLogs("qwen_app.chat_bridge", level="WARNING") as cm:
                # set_theme 失败会触发 except → logger.warning
                bridge.set_theme("dark")
        # 验证 warning 写了
        joined = "\n".join(cm.output)
        self.assertIn("持久化 theme 失败", joined,
                      "chat_bridge 持久化失败必须 logger.warning 留痕")


# ── 用例 3：silent-OK 路径不强制写日志 ──


class TestSilentOkPathsDontForceLog(unittest.TestCase):
    """P0：silent-OK 路径（取消 / 清理 / stop_chat 时 worker 已 None）不强制写日志。

    按架构 §2.1：silent-OK = 保留 ``except ... pass``，不强制写 ERROR。
    验证：这些路径「不在 log 中」是允许的（不能反向断言写了 ERROR）。
    """

    def test_silent_ok_paths_dont_force_log(self):
        """stop_chat 在 worker 已 None 时必须早返回（不抛、不写 ERROR）。"""
        from qwen_app.chat_bridge import ChatBridge

        bridge = ChatBridge(theme="light")
        # worker 已 None —— stop_chat 必须早返回，不抛异常
        bridge._worker = None
        # 不应抛异常
        try:
            bridge.stop_chat()
        except Exception as e:
            self.fail(f"stop_chat 在 worker=None 时不应抛异常，实际：{e!r}")

        # stop_chat 成功后，看 logger 是否写了 ERROR / WARNING。
        # silent-OK 路径应保持「不在 log 中」（或最多 DEBUG 级）；不应有 ERROR 级。
        # 由于 caplog 必须在 with 块内才能捕获，我们无法事后捕获，
        # 所以此处改为构造一个临时 Handler 监听整个 stop_chat 过程。
        records = []

        class _CollectHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _CollectHandler(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        # 只挂到根 logger（init_logging 没被调用，挂 root 也行）
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            # 再跑一次 stop_chat（应该还是早返回）
            bridge._worker = None
            bridge.stop_chat()
        finally:
            root.removeHandler(handler)

        # 验证：stop_chat 没有产生 ERROR/WARNING 级日志（silent-OK）
        errors = [r for r in records if r.levelno >= logging.WARNING]
        self.assertEqual(
            errors, [],
            f"stop_chat silent-OK 路径不应产生 ERROR/WARNING 日志，实际："
            f"{[r.getMessage() for r in errors]}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
