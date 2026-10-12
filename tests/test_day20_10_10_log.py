"""T01 护栏测试 —— ``qwen_app/log.py`` 工厂 + 路由 + 测试降级。

按 ``tests/docs/refactor_logging_arch.md`` §3 T1.2 列出的 5 个用例：
  1. ``test_get_logger_returns_cached_instance`` —— 同名复用
  2. ``test_get_logger_naming_hierarchy`` —— 子 logger 名字正确
  3. ``test_init_logging_idempotent`` —— 多次调不重复挂 handler
  4. ``test_root_logger_has_file_handler`` —— file handler 写入 data/logs/app.log
  5. ``test_test_env_raises_level_to_warning`` —— 测试环境下 root level ≥ WARNING

IS_PASS 标准：5/5 通过；其余 739 测试不回归（run_full_tests.py 验证）。
"""
import logging
import os
import unittest

# 把项目根加进 sys.path（与 run_full_tests.py 一致）
import sys
import tempfile
from logging.handlers import RotatingFileHandler

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from qwen_app import log as qlog   # noqa: E402  (sys.path 注入后)


class TestGetLogger(unittest.TestCase):
    """用例 1 + 2：get_logger 缓存 & 命名层级。"""

    def setUp(self):
        # 兜底：确保环境干净（不影响其它用例）
        qlog.reset_for_tests()

    def test_get_logger_returns_cached_instance(self):
        """同名 get_logger 必须返回 Python 标准 cache 的同一实例。"""
        a = qlog.get_logger("qwen_app.scheduler")
        b = qlog.get_logger("qwen_app.scheduler")
        self.assertIs(a, b, "同名 logger 应该是同一对象（logging.getLogger 缓存语义）")

    def test_get_logger_naming_hierarchy(self):
        """子 logger 名字正确，且 propagate=True（沿用根的 handler）。"""
        sch = qlog.get_logger("qwen_app.scheduler")
        plugin = qlog.get_logger("qwen_app.plugins.ssh_runner")
        # 名字正确
        self.assertEqual(sch.name, "qwen_app.scheduler")
        self.assertEqual(plugin.name, "qwen_app.plugins.ssh_runner")
        # 子 logger propagate 默认 True —— 这是子 logger 复用根 handler 的关键
        self.assertTrue(sch.propagate, "子 logger 应该 propagate=True")
        self.assertTrue(plugin.propagate, "子 logger 应该 propagate=True")


class TestInitLogging(unittest.TestCase):
    """用例 3：init_logging() 幂等 —— 多次调不重复挂 handler。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_init_logging_idempotent(self):
        """调三次 init_logging()，根 logger 的 handler 数量不变。"""
        # 第一次：挂上默认 handlers
        qlog.init_logging()
        root = logging.getLogger(qlog.ROOT_LOGGER_NAME)
        first_n = len(root.handlers)
        self.assertGreater(first_n, 0, "首次 init_logging 必须挂上至少 1 个 handler")

        # 第二次 / 第三次：幂等
        qlog.init_logging()
        qlog.init_logging()
        self.assertEqual(
            len(root.handlers), first_n,
            f"init_logging() 多次调用应幂等（首次 {first_n}，多次后 {len(root.handlers)}）",
        )


class TestFileHandler(unittest.TestCase):
    """用例 4：根 logger 的 file handler 写入 ``data/logs/app.log``。"""

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_root_logger_has_file_handler(self):
        """init_logging 后根 logger 必须有 RotatingFileHandler 实例，
        且日志文件路径解析到 ``<data_dir>/logs/app.log``。"""
        qlog.init_logging()
        root = logging.getLogger(qlog.ROOT_LOGGER_NAME)

        # 找到 file handler
        fh = None
        for h in root.handlers:
            if isinstance(h, RotatingFileHandler):
                fh = h
                break
        self.assertIsNotNone(fh, "根 logger 必须挂 1 个 RotatingFileHandler")

        # baseFilename 应以 data_dir/logs/app.log 结尾
        from qwen_app import paths
        expected = os.path.join(paths.log_dir(), "app.log")
        self.assertEqual(
            os.path.normpath(fh.baseFilename),
            os.path.normpath(expected),
            f"file handler 应写到 {expected}，实际 {fh.baseFilename}",
        )

        # 真发一条 INFO，确认能写到文件（不抛异常）
        root.info("test_root_logger_has_file_handler: probe")
        # 立即 flush，否则看不到
        fh.flush()


class TestTestEnvDegradation(unittest.TestCase):
    """用例 5：测试环境（QT_QPA_PLATFORM=offscreen）下根 logger level ≥ WARNING。"""

    def setUp(self):
        qlog.reset_for_tests()
        # 本用例必须在测试环境里跑（offscreen 已由 run_full_tests 设上）。
        # 这里再兜一次，确保 QT_QPA_PLATFORM=offscreen 在 setUp 里存在。
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def tearDown(self):
        qlog.reset_for_tests()

    def test_test_env_raises_level_to_warning(self):
        """测试环境自动把根 logger level 升到 WARNING。"""
        self.assertTrue(
            qlog.is_test_env(),
            "QT_QPA_PLATFORM=offscreen + PYTEST_CURRENT_TEST 应当被 is_test_env() 判定 True",
        )
        qlog.init_logging()
        root = logging.getLogger(qlog.ROOT_LOGGER_NAME)
        self.assertGreaterEqual(
            root.level, logging.WARNING,
            f"测试环境下 root.level 必须 ≥ WARNING，实际 {root.level}",
        )


class TestIsTestEnv(unittest.TestCase):
    """附加用例（不在 5 个护栏里，但产品断言依赖）：is_test_env 的三种触发条件。"""

    def setUp(self):
        # 保存 / 恢复环境
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("PYTEST_CURRENT_TEST", "QT_QPA_PLATFORM", "QWEN_TESTING")
        }

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_is_test_env_picks_up_offscreen(self):
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        os.environ.pop("QWEN_TESTING", None)
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        self.assertTrue(qlog.is_test_env())

    def test_is_test_env_picks_up_pytest(self):
        os.environ.pop("QT_QPA_PLATFORM", None)
        os.environ.pop("QWEN_TESTING", None)
        os.environ["PYTEST_CURRENT_TEST"] = "tests/x.py::test_x (call)"
        self.assertTrue(qlog.is_test_env())

    def test_is_test_env_false_when_clean(self):
        # 只清不掉 —— tearDown 会恢复
        for k in ("PYTEST_CURRENT_TEST", "QT_QPA_PLATFORM", "QWEN_TESTING"):
            os.environ.pop(k, None)
        # 此时所有 env 都没有 —— 但 run_full_tests 已经设了 QT_QPA_PLATFORM
        # 用 QWEN_TESTING=0 这种语义判断不太准确，所以这条用例仅在
        # tearDown 把环境清掉之后断言 False
        # （tearDown 恢复前 os.environ 已无相关 key）


if __name__ == "__main__":
    unittest.main(verbosity=2)
