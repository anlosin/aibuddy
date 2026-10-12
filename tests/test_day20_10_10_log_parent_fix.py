# -*- coding: utf-8 -*-
"""P0 Bug 修复护栏 —— ``init_logging()`` parent chain 自修复。

修复背景（QA 独立验证发现）：
  ``qwen_app/scheduler.py`` 等业务模块顶部有::

      logger = get_logger("qwen_app.scheduler")

  模块导入发生在 ``init_logging()`` 之前（main.py 启动顺序），因此 Python
  logging 建好的 parent 链是 ``qwen_app.scheduler`` → ``qwen_app`` → ``root``。
  ``init_logging()`` 创建了 ``qwen`` 根 logger，但已存在的子 logger 不会
  自动改 parent，导致 ``logger.info()`` **永远到不了** ``data/logs/app.log``
  （file handler 在 ``qwen`` 上）。

修复：在 ``init_logging()`` 末尾调 ``reparent_existing_qwen_loggers``，
把 ``logging.root.manager.loggerDict`` 里所有处于断裂链顶层的
``qwen_app*`` / ``qwen.*`` logger 的 ``parent`` 重新挂到 ``qwen`` 根 logger 上
（中间层与子 logger 沿 propagate 自动连上，**保持 Python logging 自然层级**）。

本文件用例（≥3）：
  1. ``test_init_logging_reparents_topmost_qwen_app_loggers`` ——
     强制把 qwen_app/qwen_app.* logger parent 重置为"init 前"断裂链状态 → 调
     init_logging → 断言顶层 ancestor 的 parent 变成 qwen；子 logger 通过
     propagate 自动连到 qwen。
  2. ``test_init_logging_reparents_loggers_created_after_import`` ——
     先 init_logging → 再创建新 qwen_app.* 子 logger → 断言新子 logger 的
     parent 链上游能到 qwen。
  3. ``test_child_logger_info_lands_in_app_log`` —— 与 QA 失败的测试同款：
     验证 ``qwen_app.scheduler.info("PROBE")`` 真的写到 ``data/logs/app.log``。
  4. ``test_reparent_idempotent`` —— 重复调 reparent 不抛、不破坏 handler 数量。
  5. ``test_reparent_does_not_touch_other_loggers`` —— 不动 ``pyqt5`` /
     ``urllib3`` 等第三方 logger。
  6. ``test_reparent_preserves_natural_hierarchy`` —— 中间层
     ``qwen_app.bridge`` 的 parent 仍是 ``qwen_app``（不强行 flatten）。

IS_PASS 标准：6/6 通过；run_full_tests.py 全量 0 回归。
"""
import logging
import os
import sys
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qwen_app import log as qlog  # noqa: E402


def _flush_all_handlers():
    """flush 根 logger 上所有 handler（让 probe 真的写到磁盘）。"""
    root = logging.getLogger(qlog.ROOT_LOGGER_NAME)
    for h in root.handlers:
        if hasattr(h, "flush"):
            try:
                h.flush()
            except Exception:
                pass


def _simulate_pre_init_state():
    """模拟 main.py 启动顺序：模块顶部 logger 已创建，'qwen' logger 还没创建，
    parent 链处于断裂状态（qwen_app.parent = root，qwen_app.<X>.parent = qwen_app）。

    返回被重置过的 logger 名字集合。
    """
    # 强依赖：业务模块顶部 `logger = get_logger(...)` 已执行
    # 触发 import（如果还没 import）
    try:
        import qwen_app.scheduler  # noqa: F401
    except Exception:
        pass
    try:
        import qwen_app.chat_bridge  # noqa: F401
    except Exception:
        pass
    try:
        import qwen_app.plugin_manager  # noqa: F401
    except Exception:
        pass

    python_root = logging.getLogger("root")
    qwen_app_placeholder = logging.getLogger("qwen_app")
    touched = set()
    for name in list(logging.root.manager.loggerDict.keys()):
        if name == qlog.ROOT_LOGGER_NAME:
            continue
        if not (name == "qwen_app"
                or name.startswith("qwen_app.")
                or name.startswith("qwen.")):
            continue
        child = logging.getLogger(name)
        # 顶层 ancestor：parent 重置为 root（断裂状态）
        if name == "qwen_app" or name.startswith("qwen."):
            if child is not python_root:
                child.parent = python_root
                touched.add(name)
        # 中间层 / 叶子：parent 重置为 qwen_app（占位，parent 是 root）
        elif name.startswith("qwen_app."):
            if child is not qwen_app_placeholder:
                child.parent = qwen_app_placeholder
                touched.add(name)
    return touched


# ───────────────────────────────────────────────────────────────────────
# 用例 1：init_logging 把 qwen_app 顶层 ancestor 的 parent 挂到 qwen 上
# ───────────────────────────────────────────────────────────────────────


class TestInitLoggingReparentsTopmostQwenAppLoggers(unittest.TestCase):
    """P0 修复核心断言：强制把 parent 链重置为 init 前的断裂状态 → 调
    init_logging → 断言顶层 ancestor 的 parent 是 ``qwen``。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_init_logging_reparents_topmost_qwen_app_loggers(self):
        """模拟 main.py 真实启动顺序：先 import 业务模块（模块顶部 logger 创建，
        parent 链是 qwen_app.* → qwen_app → root）→ 调 init_logging → 断言
        顶层 ancestor qwen_app 的 parent.name 是 "qwen"。
        """
        # 1. 强制把 parent 链重置为 "init 前" 断裂状态
        _simulate_pre_init_state()
        sch_log = logging.getLogger("qwen_app.scheduler")
        bridge_log = logging.getLogger("qwen_app.chat_bridge")
        qwen_app_log = logging.getLogger("qwen_app")

        # 2. 确认断裂状态：qwen_app.parent = root；sch.parent = qwen_app
        self.assertEqual(
            qwen_app_log.parent.name, "root",
            f"断裂状态下 qwen_app.parent 应是 root，实际 {qwen_app_log.parent.name}"
        )
        self.assertEqual(
            sch_log.parent.name, "qwen_app",
            f"断裂状态下 qwen_app.scheduler.parent 应是 qwen_app，实际 {sch_log.parent.name}"
        )
        # 业务侧还没有 'qwen' logger
        self.assertEqual(
            logging.getLogger("qwen").parent.name, "root",
            f"断裂状态下 qwen.parent 应是 root（占位），实际 {logging.getLogger('qwen').parent.name}"
        )

        # 3. init_logging —— 触发 reparent
        qlog.init_logging()
        _flush_all_handlers()

        # 4. 核心断言：顶层 ancestor qwen_app 的 parent 是 qwen
        self.assertEqual(
            qwen_app_log.parent.name, "qwen",
            f"init 后 qwen_app.parent.name 必须是 'qwen'，实际 {qwen_app_log.parent.name!r}"
        )
        # 子 logger 沿 propagate 经 qwen_app → qwen 连上
        self.assertEqual(
            sch_log.parent.name, "qwen_app",
            f"qwen_app.scheduler.parent 应保持 qwen_app（自然层级），实际 {sch_log.parent.name}"
        )
        self.assertEqual(
            bridge_log.parent.name, "qwen_app",
            f"qwen_app.chat_bridge.parent 应保持 qwen_app（自然层级），实际 {bridge_log.parent.name}"
        )
        # 完整 propagate 链：sch → qwen_app → qwen（handlers）
        # 验证最终上游是 qwen
        cur = sch_log.parent
        chain = [cur.name]
        while cur.parent is not None and cur.parent.name != "root":
            cur = cur.parent
            chain.append(cur.name)
        self.assertEqual(
            chain[-1], "qwen",
            f"qwen_app.scheduler 的 parent 链末端必须是 qwen，实际链 {chain}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 2：init 后再创建的 qwen_app.* 子 logger 通过 propagate 自动连到 qwen
# ───────────────────────────────────────────────────────────────────────


class TestInitLoggingReparentsLoggersCreatedAfterImport(unittest.TestCase):
    """init 之后再 getLogger 创建新的 qwen_app.* 子 logger，Python 默认
    _fixupParents 会找到已存在的 ``qwen_app`` logger（其 parent 已被
    reparent 成 qwen）→ 新 logger 的 parent 链会经过 qwen_app → qwen。

    验证这条隐含路径也走通 —— 防止哪天有人改了 ``qwen_app`` 自身的 parent
    链而漏掉修子 logger。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_init_logging_reparents_loggers_created_after_import(self):
        """先 init_logging() → 再创建新 qwen_app.* 子 logger → 断言
        新子 logger 的 parent 链上游能到 ``qwen``（关键：Python logging 的
        ``_fixupParents`` 一旦发现 ancestor 在 dict 里就**立即**用作 parent，
        不经过中间 PlaceHolder；所以新 leaf 的 parent 直接是已存在的
        ``qwen_app``，不是新建的 ``qwen_app.bridge``）。
        """
        # init 先（模拟 test 环境 init 顺序）
        qlog.init_logging()
        _flush_all_handlers()

        # 确认 qwen_app 顶层 ancestor 已被 reparent 到 qwen
        qwen_app_log = logging.getLogger("qwen_app")
        self.assertEqual(
            qwen_app_log.parent.name, "qwen",
            f"qwen_app.parent 应已被 reparent 到 qwen，实际 {qwen_app_log.parent.name}"
        )

        # 现在创建一个全新的子 logger（模拟 import time 不同的模块）
        leaf = logging.getLogger("qwen_app.bridge.bubble")

        # Python logging 行为：_fixupParents 在找到 dict 中已存在的 ancestor 时
        # 立即用作 parent；本例中 qwen_app 在 dict 里 → leaf.parent = qwen_app
        # （不经过 qwen_app.bridge PlaceHolder）。
        self.assertEqual(
            leaf.parent.name, "qwen_app",
            f"qwen_app.bridge.bubble.parent 应是 qwen_app（Python _fixupParents "
            f"找到最近 ancestor 后立即用作 parent），实际 {leaf.parent.name}"
        )
        # 验证 propagate 链能到 qwen
        cur = leaf.parent
        chain = [cur.name]
        while cur.parent is not None and cur.parent.name != "root":
            cur = cur.parent
            chain.append(cur.name)
        self.assertEqual(
            chain, ["qwen_app", "qwen"],
            f"qwen_app.bridge.bubble 的 parent 链末端必须是 qwen，实际链 {chain}"
        )
        self.assertEqual(
            chain[-1], "qwen",
            f"qwen_app.bridge.bubble 的 parent 链末端必须是 qwen，实际 {chain}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 3：业务侧 logger.info() 真落到 app.log（QA 同款失败场景）
# ───────────────────────────────────────────────────────────────────────


class TestChildLoggerInfoLandsInAppLog(unittest.TestCase):
    """P0 行为 —— 业务侧 ``qwen_app.scheduler.logger.info()`` 必须 propagate 到
    ``qwen`` 根 logger 的 handler，最终写到 ``data/logs/app.log``。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_child_logger_info_lands_in_app_log(self):
        """模拟 main.py 真实启动顺序：先 import 业务模块 → init_logging() →
        业务 logger.info("PROBE") 真的写到 app.log。
        """
        # 1. 强制 parent 链到"init 前"断裂状态
        _simulate_pre_init_state()

        # 2. reset + init（模拟 main.py 启动期 init 一次）
        qlog.reset_for_tests()
        qlog.init_logging()
        _flush_all_handlers()

        # 3. 强制 root level = INFO（防止 is_test_env 把级别升到 WARNING）
        qwen = logging.getLogger(qlog.ROOT_LOGGER_NAME)
        qwen.setLevel(logging.INFO)

        # 4. 二次断言：parent 修复正确（用例 1 单独跑过）
        sch_log = logging.getLogger("qwen_app.scheduler")
        self.assertEqual(
            sch_log.parent.name, "qwen_app",
            f"修复后 qwen_app.scheduler.parent 应是 qwen_app（自然层级），实际 {sch_log.parent.name!r}"
        )
        qwen_app_log = logging.getLogger("qwen_app")
        self.assertEqual(
            qwen_app_log.parent.name, "qwen",
            f"修复后 qwen_app.parent 应是 qwen，实际 {qwen_app_log.parent.name!r}"
        )

        # 5. 写独特 probe（hex 串，确保未被其他地方命中）
        probe_marker = "P0FIX_PROBE_8842DEADBEEF_MARKER"
        sch_log.info("%s", probe_marker)
        _flush_all_handlers()

        # 6. 验证 probe 真落 app.log
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


# ───────────────────────────────────────────────────────────────────────
# 用例 4：reparent 幂等 —— 不会重复挂 handler / 不抛异常
# ───────────────────────────────────────────────────────────────────────


class TestReparentIdempotent(unittest.TestCase):
    """``reparent_existing_qwen_loggers()`` 必须幂等：
      - 第二次调 = no-op（所有顶层 ancestor parent 已是 qwen）。
      - 不重复挂 handler（root.handlers 数量不变）。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_reparent_idempotent(self):
        """连续调 reparent 两次 → 第二次返回 0；不破坏 qwen 根 logger handler 数量。"""
        _simulate_pre_init_state()
        qlog.init_logging()
        _flush_all_handlers()

        qwen = logging.getLogger(qlog.ROOT_LOGGER_NAME)
        n_handlers_before = len(qwen.handlers)

        # 第一次 reparent（在 init 末尾已经跑过一次；显式再跑也应幂等）
        count1 = qlog.reparent_existing_qwen_loggers(qwen)
        # 第二次 reparent
        count2 = qlog.reparent_existing_qwen_loggers(qwen)
        self.assertEqual(
            count2, 0,
            f"第二次 reparent 应返回 0（已无待重挂 logger），实际 {count2}"
        )
        # handler 数量不变
        n_handlers_after = len(qwen.handlers)
        self.assertEqual(
            n_handlers_before, n_handlers_after,
            f"reparent 不应动 qwen 根 handler 数量：before={n_handlers_before}, after={n_handlers_after}"
        )

    def test_init_logging_idempotent_after_reparent(self):
        """init_logging() 多次调（外层幂等守卫）→ reparent 不会重复挂 handler。"""
        _simulate_pre_init_state()
        qlog.init_logging()
        n1 = len(logging.getLogger(qlog.ROOT_LOGGER_NAME).handlers)
        for _ in range(5):
            qlog.init_logging()
        n2 = len(logging.getLogger(qlog.ROOT_LOGGER_NAME).handlers)
        self.assertEqual(
            n1, n2,
            f"init_logging() 多次调 handler 数不变：{n1} → {n2}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 5：reparent 不动其它库的 logger（命名空间边界）
# ───────────────────────────────────────────────────────────────────────


class TestReparentDoesNotTouchOtherLoggers(unittest.TestCase):
    """``reparent_existing_qwen_loggers`` 只动 ``qwen_app*`` / ``qwen.*`` 前缀，
    不污染 ``pyqt5`` / ``urllib3`` / ``asyncio`` 等其它命名空间。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_reparent_does_not_touch_other_loggers(self):
        """创建 ``pyqt5.foo`` / ``urllib3.connectionpool`` 等第三方风格 logger，
        跑 reparent，断言它们的 parent 不被改。"""
        # 先触发业务模块 import（确保 reparent 会动它）
        _simulate_pre_init_state()
        # 创建几个非业务 logger
        pyqt5 = logging.getLogger("pyqt5.something")
        urllib3 = logging.getLogger("urllib3.connectionpool")
        asyncio_log = logging.getLogger("asyncio")
        # 第三方 logger 强制 parent = root（如果它之前被 reparent 过）
        python_root = logging.getLogger("root")
        for lg in (pyqt5, urllib3, asyncio_log):
            lg.parent = python_root

        parent_before = {
            "pyqt5.something": pyqt5.parent.name if pyqt5.parent else None,
            "urllib3.connectionpool": urllib3.parent.name if urllib3.parent else None,
            "asyncio": asyncio_log.parent.name if asyncio_log.parent else None,
        }

        # init
        qlog.init_logging()
        _flush_all_handlers()

        parent_after = {
            "pyqt5.something": pyqt5.parent.name if pyqt5.parent else None,
            "urllib3.connectionpool": urllib3.parent.name if urllib3.parent else None,
            "asyncio": asyncio_log.parent.name if asyncio_log.parent else None,
        }

        self.assertEqual(
            parent_before, parent_after,
            f"reparent 不应改非 qwen_app*/qwen.* logger 的 parent：\n"
            f"  before={parent_before}\n"
            f"  after ={parent_after}"
        )
        # 业务 logger 应被改到 qwen（顶层 qwen_app.parent.name = qwen）
        qwen_app_log = logging.getLogger("qwen_app")
        self.assertEqual(
            qwen_app_log.parent.name, "qwen",
            f"qwen_app.parent 应是 qwen，实际 {qwen_app_log.parent.name}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 6：reparent 不强行 flatten —— 保持 Python logging 自然层级
# ───────────────────────────────────────────────────────────────────────


class TestReparentPreservesNaturalHierarchy(unittest.TestCase):
    """``reparent_existing_qwen_loggers`` 只 reparent 顶层 ancestor（parent 是
    root 的），中间层（parent 是 qwen_app / qwen.x）保持原样 —— 这样
    ``qwen_app.bridge`` 的 parent 仍是 ``qwen_app``，不被强行改成 ``qwen``。
    """

    def setUp(self):
        qlog.reset_for_tests()

    def tearDown(self):
        qlog.reset_for_tests()

    def test_reparent_preserves_natural_hierarchy(self):
        """强制中间层 qwen_app.bridge 的 parent = qwen_app（自然层级）→ init
        → 断言 qwen_app.bridge.parent 仍是 qwen_app（不被改成 qwen）。"""
        # 触发 import + 强制中间层 parent 状态
        _simulate_pre_init_state()
        # qwen_app.bridge 中间层可能没被创建；显式创建并设 parent = qwen_app
        mid = logging.getLogger("qwen_app.bridge")
        qwen_app_log = logging.getLogger("qwen_app")
        # 把 qwen_app.parent 暂时改回 root 以模拟 "before init" 状态
        qwen_app_log.parent = logging.getLogger("root")
        # qwen_app.bridge.parent 应该是 qwen_app（这是正常的中间层状态）
        if mid.parent.name != "qwen_app":
            mid.parent = qwen_app_log

        before_mid_parent = mid.parent.name  # 应是 qwen_app

        qlog.init_logging()
        _flush_all_handlers()

        # 中间层 parent 保持 qwen_app（自然层级）
        self.assertEqual(
            mid.parent.name, "qwen_app",
            f"qwen_app.bridge.parent 应保持 qwen_app（不被 flatten），"
            f"before={before_mid_parent}，after={mid.parent.name}"
        )
        # 顶层 ancestor qwen_app 的 parent 应被改成 qwen
        self.assertEqual(
            qwen_app_log.parent.name, "qwen",
            f"qwen_app.parent 应被 reparent 到 qwen，实际 {qwen_app_log.parent.name}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)