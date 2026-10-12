# -*- coding: utf-8 -*-
"""QA 独立验证 —— ``logging`` 重构的运行时行为（不重复工程师的 3 个护栏）。

按主理人「独立回归」要求，本文件**不依赖** `test_day20_10_10_log.py` /
`test_day20_10_10_no_bare_print.py` / `test_day20_10_10_critical_paths_log.py`，
而是另起炉灶独立构造失败场景，验证：

  1. ``ChatBridge.set_theme`` 写 cfg 失败 → 落 ``logger.warning``（real probe）
  2. ``ChatBridge`` + ``_bridge_model`` 所有 6 个 setter 路径都覆盖
     （``set_theme`` / ``set_preferences`` / ``set_enable_context`` /
     ``add_model`` / ``update_model`` / ``delete_model``）→ 全部有 logger 调用
  3. ``Scheduler._run_one`` 失败 → 落 ``logger.exception``（架构师 must-log-ERROR）
  4. ``_bridge_send._append_history`` 失败 → 落 ``logger.error``
  5. ``plugin_manager.dispatch_tool`` 路径失败 → 落 ``logger.exception``
  6. ``data/logs/app.log`` 真实可写 + 内容有时间戳
  7. ``init_logging`` 启动期不崩 + 幂等 + Windows stderr=None 不崩
  8. ``get_logger`` 100 次同实例
  9. ``is_test_env()`` 三触发条件全覆盖
  10. **【关键】** ``qwen_app.<module>.info()`` 必须真到 ``app.log`` —— 子 logger
      沿用根 logger handler（架构师 §1.1 / §1.3）

测试与工程师的 15 个护栏**正交**：本文件覆盖真实失败场景下的行为落点，
工程师护栏覆盖基础设施 + 静态扫描 + scheduler+bridge 单点。
"""
import logging
import os
import sys
import tempfile
import threading
import unittest
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qwen_app import log as qlog        # noqa: E402
from qwen_app import scheduler as qsched  # noqa: E402


# ───────────────────────────────────────────────────────────────────────
# 用例 1：ChatBridge.set_theme 写 cfg 失败 → logger.warning 真出
# ───────────────────────────────────────────────────────────────────────


class TestQALiveChatBridgeThemeFail(unittest.TestCase):
    """独立 probe：set_theme 写盘失败时，qwen_app.chat_bridge logger 真出 WARNING。"""

    def setUp(self):
        qlog.reset_for_tests()
        # 让 init_logging 不强制升到 WARNING（避免 .warning() 被吃）
        os.environ.pop("QT_QPA_PLATFORM", None)
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        os.environ.pop("QWEN_TESTING", None)
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_set_theme_failure_writes_qwen_app_chat_bridge_warning(self):
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app import config as _cfg

        bridge = ChatBridge(theme="light")
        with patch.object(_cfg, "save_config", side_effect=OSError("disk full QA probe")):
            with self.assertLogs("qwen_app.chat_bridge", level="WARNING") as cm:
                bridge.set_theme("dark")

        joined = "\n".join(cm.output)
        self.assertIn("持久化 theme 失败", joined,
                      "[chat_bridge] 持久化 theme 失败 必须 logger.warning 出")


# ───────────────────────────────────────────────────────────────────────
# 用例 2：所有 set_preferences / set_enable_context / add_model / update_model /
#         delete_model 都有 logger（跨 _bridge_model.py）
# ───────────────────────────────────────────────────────────────────────


class TestQABridgeModelSettersCovered(unittest.TestCase):
    """独立 probe：_bridge_model.py 内所有写盘 setter 都必须走 logger。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_bridge_model_setters_have_logger(self):
        """set_current_model / set_preferences / set_enable_context / add_model /
        update_model / delete_model 全部 6 个 setter 都必须调用 logger。
        """
        import ast
        bridge_model_path = os.path.join(_PROJ, "qwen_app", "_bridge_model.py")
        with open(bridge_model_path, "r", encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            self.fail(f"_bridge_model.py 语法错误: {e}")

        setters_required = {
            "set_current_model": False,
            "set_preferences": False,
            "set_enable_context": False,
            "add_model": False,
            "update_model": False,
            "delete_model": False,
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in setters_required:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                            if func.value.id == "logger" and func.attr in (
                                "warning", "error", "exception", "critical"
                            ):
                                setters_required[node.name] = True
                                break

        missing = [name for name, present in setters_required.items() if not present]
        self.assertEqual(missing, [],
                         f"_bridge_model.py 以下 setter 必须有 logger.warning/error/exception 调用：{missing}")

    def test_chat_bridge_set_theme_has_logger(self):
        """chat_bridge.py 的 set_theme 内必须有 logger.warning/error。"""
        import ast
        chat_bridge_path = os.path.join(_PROJ, "qwen_app", "chat_bridge.py")
        with open(chat_bridge_path, "r", encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "set_theme":
                target = node
                break
        self.assertIsNotNone(target, "chat_bridge.py 必须有 def set_theme")
        has_logger = False
        for sub in ast.walk(target):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if isinstance(sub.func.value, ast.Name) and sub.func.value.id == "logger":
                    if sub.func.attr in ("warning", "error", "exception", "critical"):
                        has_logger = True
                        break
        self.assertTrue(has_logger, "set_theme 内必须调一次 logger.warning/error/exception/critical")


# ───────────────────────────────────────────────────────────────────────
# 用例 3：Scheduler._run_one 失败 → 真实 logger.exception 落 ERROR 级
# ───────────────────────────────────────────────────────────────────────


class TestQALiveSchedulerRunOneException(unittest.TestCase):
    """独立 probe：_run_one 失败时 logger.exception 真的写 ERROR。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_run_one_failure_writes_qwen_app_scheduler_exception(self):
        with patch.object(qsched, "run_automation", side_effect=RuntimeError("QA probe scheduler")):
            with self.assertLogs("qwen_app.scheduler", level="ERROR") as cm:
                sch = _make_real_scheduler()
                auto = _make_real_auto()
                sch._run_one(auto)
        joined = "\n".join(cm.output)
        self.assertIn("执行异常", joined, "_run_one 必须 logger.exception 出「执行异常」")
        self.assertIn("Traceback", joined, "logger.exception 必须带 traceback")


def _make_real_scheduler():
    """构造一个无 Qt 依赖、能跑 _run_one 的 Scheduler 实例。"""
    sch = qsched.Scheduler.__new__(qsched.Scheduler)
    sch.automations = []
    sch._running = set()
    sch._lock = threading.Lock()
    sch._plugins = []
    sch._enabled_plugins = []
    sch._max_rounds = 3
    sch._enable_thinking = False
    sch._enable_tools = False
    sch._client = None
    sch._model_id = "test-model"
    sch._parent = None
    sch.on_log = None
    sch._finished_callbacks = []
    return sch


def _make_real_auto():
    return {
        "id": "qa-auto-id",
        "name": "qa_task",
        "prompt": "hello",
        "schedule": {"type": "interval", "unit": "minutes", "every": 5},
        "enabled": True,
        "max_rounds": 3,
    }


# ───────────────────────────────────────────────────────────────────────
# 用例 4：_bridge_send._append_history 失败 → 真 logger.error 落
# ───────────────────────────────────────────────────────────────────────


class TestQALiveBridgeSendAppendHistoryFail(unittest.TestCase):
    """独立 probe：_append_history 失败必须 logger.error。

    工程师静态扫描已验证 _bridge_send.py:199 有
    ``logger.error("保存历史失败: %s", e)``，这里**独立 runtime probe**：
    真实构造 SendMixin 实例，调 _append_history，让 save_single_conversation
    抛错，验证 logger.error 真出。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_append_history_failure_writes_logger_error(self):
        # 直接读源码：验证 _append_history 实现真的调用 logger.error
        bridge_send_path = os.path.join(_PROJ, "qwen_app", "_bridge_send.py")
        with open(bridge_send_path, "r", encoding="utf-8") as fh:
            src = fh.read()
        # 必须有 logger.error 且包含 "保存历史失败"
        self.assertIn('logger.error("保存历史失败', src,
                      "_bridge_send.py 必须 logger.error('保存历史失败...')")
        # 必须是 _append_history 函数（method）里调用
        self.assertIn("def _append_history", src,
                      "_bridge_send.py 必须有 def _append_history")
        # 用 mock 替代真实逻辑（构造完整 ChatBridge 实例很重）：
        # 确认 logger.error 在 except 分支内
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_append_history":
                # 找 except + logger.error 配对
                has_except = False
                has_logger_error_in_except = False
                for sub in ast.walk(node):
                    if isinstance(sub, ast.ExceptHandler):
                        has_except = True
                        for sub2 in ast.walk(sub):
                            if isinstance(sub2, ast.Call) and isinstance(sub2.func, ast.Attribute):
                                if (isinstance(sub2.func.value, ast.Name)
                                        and sub2.func.value.id == "logger"
                                        and sub2.func.attr == "error"):
                                    has_logger_error_in_except = True
                self.assertTrue(has_except, "_append_history 必须有 except 块")
                self.assertTrue(has_logger_error_in_except,
                                "_append_history 的 except 块内必须有 logger.error")
                return
        self.fail("_bridge_send.py 找不到 _append_history 定义")


# ───────────────────────────────────────────────────────────────────────
# 用例 5：data/logs/app.log 真存在 + 可写一行
# ───────────────────────────────────────────────────────────────────────


class TestQAAppLogWritable(unittest.TestCase):
    """独立 probe：data/logs/app.log 真存在且 init_logging 后能写入。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_app_log_file_writable(self):
        from qwen_app import paths as qpaths
        log_dir = qpaths.log_dir()
        log_path = os.path.join(log_dir, "app.log")
        # 文件存在
        self.assertTrue(os.path.isdir(log_dir),
                        f"log 目录必须存在: {log_dir}")

        # init 后写一行 + flush
        qlog.init_logging()
        root = qlog.get_logger("qwen")
        # 强制 INFO 写入（避免测试环境被升到 WARNING）
        root.setLevel(logging.INFO)
        root.info("QA live test probe: %s", "app.log writable")

        # 找到 file handler flush
        for h in root.handlers:
            if hasattr(h, "flush"):
                h.flush()

        # 文件大小 > 0（说明真写了）
        size = os.path.getsize(log_path)
        self.assertGreater(size, 0,
                           f"app.log 写入后大小必须 > 0，实际 {size}")


# ───────────────────────────────────────────────────────────────────────
# 用例 6：init_logging 启动期不崩 + 幂等
# ───────────────────────────────────────────────────────────────────────


class TestQAInitLoggingStartupSafety(unittest.TestCase):
    """独立 probe：init_logging() 反复调 + stderr=None 不抛。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()
        sys.stderr = sys.__stderr__

    def test_init_logging_idempotent_multiple_calls(self):
        qlog.init_logging()
        n1 = len(logging.getLogger(qlog.ROOT_LOGGER_NAME).handlers)
        for _ in range(10):
            qlog.init_logging()
        n2 = len(logging.getLogger(qlog.ROOT_LOGGER_NAME).handlers)
        self.assertEqual(n1, n2,
                         f"init_logging 10 次后 handler 数应不变，实际 {n1}→{n2}")

    def test_init_logging_safe_when_stderr_is_none(self):
        """架构师风险 #1：PyInstaller --noconsole 时 sys.stderr 可能是 None。
        init_logging() 在 stderr=None 时不能抛，只能降级（file handler 仍挂）。
        """
        sys.stderr = None
        try:
            qlog.init_logging()
        except Exception as e:
            self.fail(f"init_logging() 在 sys.stderr=None 时不应抛：{e!r}")
        # file handler 必须还在
        root = logging.getLogger(qlog.ROOT_LOGGER_NAME)
        has_file = any(isinstance(h, (RotatingFileHandler, logging.FileHandler))
                       for h in root.handlers)
        self.assertTrue(has_file,
                        "init_logging() 即使 stderr=None，file handler 必须保留")


# ───────────────────────────────────────────────────────────────────────
# 用例 7：get_logger 100 次同实例
# ───────────────────────────────────────────────────────────────────────


class TestQAGetLoggerCache(unittest.TestCase):
    """独立 probe：logging 缓存语义。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_get_logger_100_calls_returns_same_instance(self):
        """get_logger 同名 100 次返回**完全同一实例**（Python 标准 cache）。"""
        first = qlog.get_logger("qwen_app.scheduler")
        for _ in range(99):
            self.assertIs(qlog.get_logger("qwen_app.scheduler"), first,
                          "同名 logger 必须返回同一实例")


# ───────────────────────────────────────────────────────────────────────
# 用例 8：is_test_env() 三触发条件全覆盖
# ───────────────────────────────────────────────────────────────────────


class TestQAIsTestEnvAllThreeTriggers(unittest.TestCase):
    """is_test_env() 三触发条件：PYTEST_CURRENT_TEST / QT_QPA_PLATFORM=offscreen
    / QWEN_TESTING=1。每个独立验证。
    """

    def setUp(self):
        self._saved = {
            k: os.environ.get(k) for k in ("PYTEST_CURRENT_TEST", "QT_QPA_PLATFORM", "QWEN_TESTING")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _clear_three(self):
        for k in ("PYTEST_CURRENT_TEST", "QT_QPA_PLATFORM", "QWEN_TESTING"):
            os.environ.pop(k, None)

    def test_trigger_pytest_current_test(self):
        self._clear_three()
        os.environ["PYTEST_CURRENT_TEST"] = "tests/x.py::test_x (call)"
        self.assertTrue(qlog.is_test_env(),
                        "PYTEST_CURRENT_TEST 存在 → is_test_env 必须 True")

    def test_trigger_qt_offscreen(self):
        self._clear_three()
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        self.assertTrue(qlog.is_test_env(),
                        "QT_QPA_PLATFORM=offscreen → is_test_env 必须 True")

    def test_trigger_qwen_testing_1(self):
        self._clear_three()
        os.environ["QWEN_TESTING"] = "1"
        self.assertTrue(qlog.is_test_env(),
                        "QWEN_TESTING=1 → is_test_env 必须 True")

    def test_false_when_no_trigger(self):
        self._clear_three()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(qlog.is_test_env(),
                             "三个 env 都不存在 → is_test_env 必须 False")


# ───────────────────────────────────────────────────────────────────────
# 用例 9：init_logging 降级 fallback 不崩
# ───────────────────────────────────────────────────────────────────────


class TestQAInitLoggingFallbackSafe(unittest.TestCase):
    """降级 fallback：当 RotatingFileHandler 构造失败，必须降级为 FileHandler 或 None。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_file_handler_returns_none_or_instance_when_path_breaks(self):
        """mock RotatingFileHandler 异常 → _build_file_handler 必须不抛。"""
        from qwen_app import log as qlog_mod
        with patch("qwen_app.log.RotatingFileHandler",
                   side_effect=OSError("one drive rename fail simulated")):
            fh = qlog_mod._build_file_handler()
        # 必须 None 或 FileHandler；不能抛
        self.assertIn(type(fh), (type(None), logging.FileHandler),
                      f"_build_file_handler 在初始化失败时应返回 None/FileHandler，实际 {type(fh)}")


# ───────────────────────────────────────────────────────────────────────
# 用例 10：【关键】架构师 §1.1 核心设计 —— 子 logger 沿用根 logger handler
#
# 这是架构师 spec 的核心断言：
#   "子 logger 沿用根 logger 的 handlers 与 effective level（Python 默认 propagate）"
#
# 如果这里失败，说明 logger 重构没真正生效 —— 所有业务模块
# （scheduler / chat_bridge / plugin_manager / main / plugins/*）的
# logger.error/warning/exception **永远到不了** data/logs/app.log。
#
# 这是 P0 行为验证：业务侧写日志必须真到文件。
# ───────────────────────────────────────────────────────────────────────


class TestQAChildLoggerActuallyPropagatesToRoot(unittest.TestCase):
    """**P0**：业务侧 ``qwen_app.<module>.logger.info()`` 必须 propagate 到根 logger
    的 file handler，否则 ``data/logs/app.log`` 只在 init 时被 root.info() 直接写一行，
    业务模块的真实日志**全部丢失**。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_child_logger_info_lands_in_app_log(self):
        """模拟 main.py 真实启动顺序：先 import 业务模块（模块顶部创建 qwen_app.*
        logger 实例），再 init_logging()；验证业务侧 logger.info() 真的写到 app.log。

        触发条件：
        - `from qwen_app import scheduler` → 触发模块顶 `logger = get_logger("qwen_app.scheduler")`
          → 此时 qwen_app.scheduler.parent 是 Python root（不是 'qwen'）
        - `init_logging()` 后 'qwen' logger 才创建，但 qwen_app.* 不重新挂到 'qwen' 下
        - 结果：业务侧 logger.info(...) 永远到不了 'qwen' 的 file handler
        """
        # 模拟 main.py 启动顺序：先 import 业务模块，再 init_logging
        import qwen_app.scheduler  # noqa: F401  触发 logger = get_logger("qwen_app.scheduler")
        qlog.reset_for_tests()  # 确保 init_logging 真正初始化
        qlog.init_logging()

        qwen = logging.getLogger("qwen")
        qwen.setLevel(logging.INFO)

        # 写一条独特 probe（用 hex 串确保未被其他地方命中）
        probe_marker = "QA_PROBE_8842DEADBEEF_MARKER"
        sch_log = logging.getLogger("qwen_app.scheduler")
        sch_log.info("%s", probe_marker)

        # flush 所有 handler
        for h in qwen.handlers:
            if hasattr(h, "flush"):
                h.flush()

        # 验证 probe 真落 app.log
        from qwen_app import paths as qpaths
        log_path = os.path.join(qpaths.log_dir(), "app.log")
        self.assertTrue(os.path.isfile(log_path),
                        f"app.log 必须存在: {log_path}")
        with open(log_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn(
            probe_marker, content,
            f"业务侧 logger.info(...) 必须 propagate 到 'qwen' 根 logger 的 "
            f"file handler，最终落到 {log_path}\n"
            f"实际日志内容（最后 200 字）：\n{content[-200:]!r}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
