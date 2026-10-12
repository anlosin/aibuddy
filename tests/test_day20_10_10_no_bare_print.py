# -*- coding: utf-8 -*-
"""T05.1 静态扫描护栏 —— 业务代码禁止裸 ``print()``（按 docs/refactor_logging_arch.md §7）。

实现策略：用 ``ast`` 解析 T01-T04 改造范围内（scheduler.py / chat_bridge.py /
_bridge_*.py / plugin_manager.py / main.py + 关键插件 sql_helper / ssh_runner /
web_search / _secret_store），找出非 docstring / 非注释中的 ``print(...)`` 调用；
任何一处匹配 → 报错。除非该位置在 ``ALLOWED_PRINT_LOCATIONS`` 豁免清单里
（精确到 file:line）。

为什么不直接用 grep：AST 解析能区分 ``def f(): print()`` （docstring）与真调用，
不会误报 docstring 里的 ``print(1)`` 示例（参 ``plugins/_cmd_blocklist.py:20``）。

**扫描范围对齐架构师 §7 验收清单**：
- ✅ T02-T04 涉及：scheduler.py / chat_bridge.py / _bridge_*.py / plugin_manager.py /
  main.py / plugins/sql_helper.py / plugins/ssh_runner.py / plugins/web_search.py /
  plugins/_secret_store.py
- ❌ 不扫（架构师 §6 明确排除）：chat_window.py / chat_qml_window.py / chat_render.py /
  worker.py / config.py / scheduler_run.py / qml_demo.py / qml_screenshot.py
- ❌ 不扫：tests/test_*.py（fixture 用的 print）

测试用例（4 个）：
  1. ``test_no_bare_print_in_qwen_app_business`` —— T02-T04 范围内业务代码不含裸 print
  2. ``test_main_py_banner_prints_kept`` —— 启动横幅保留
  3. ``test_selftest_prints_kept`` —— ``_run_selftest`` 内 ``emit()`` 保留
  4. ``test_test_fixtures_not_scanned`` —— ``tests/test_*.py`` 不扫
"""
import ast
import os
import unittest


PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── T01-T04 改造范围内的文件（架构师 §7 验收清单）──
IN_SCOPE_FILES = [
    os.path.join(PROJ, "qwen_app", "scheduler.py"),
    os.path.join(PROJ, "qwen_app", "chat_bridge.py"),
    os.path.join(PROJ, "qwen_app", "_bridge_bubble.py"),
    os.path.join(PROJ, "qwen_app", "_bridge_send.py"),
    os.path.join(PROJ, "qwen_app", "_bridge_model.py"),
    os.path.join(PROJ, "qwen_app", "_bridge_plugin.py"),
    os.path.join(PROJ, "qwen_app", "_bridge_menu.py"),
    os.path.join(PROJ, "qwen_app", "_bridge_stream.py"),
    # _bridge_session.py 无 print（架构师 §3 批 3 已确认）
    os.path.join(PROJ, "qwen_app", "plugin_manager.py"),
    os.path.join(PROJ, "plugins", "sql_helper.py"),
    os.path.join(PROJ, "plugins", "ssh_runner.py"),
    os.path.join(PROJ, "plugins", "web_search.py"),
    os.path.join(PROJ, "plugins", "_secret_store.py"),
    os.path.join(PROJ, "main.py"),
]

MAIN_PY = os.path.join(PROJ, "main.py")

# ── 豁免清单（file:line，精确匹配）──
# 按 docs §1.5 / §7 验收清单：仅 main.py 启动横幅 + selftest emit + plugin docstring 示例
# 这些 print 是合法的（控制台用户首屏 / selftest 模式专用 / 文档示例）。
ALLOWED_PRINT_LOCATIONS = [
    # main.py 启动横幅 —— 用户首屏信号（架构师决策保留）
    "main.py:235",   # print("[main] QtQuick UI 已启动", flush=True)
    "main.py:236",   # print("[main] bridge.isBusy = ... 当前会话 = ...")
    "main.py:237",   # print("[main] 当前模型 = ...")
    "main.py:301",   # print("[main] API Key 已迁移到系统凭据库 ...")
    # main.py _run_selftest emit() —— 专门用 stdout 给运维/CI 抓取，不能改 logger
    "main.py:326",   # print(s) —— selftest emit
    # plugins/_cmd_blocklist.py docstring 里的 print(1) 示例 —— 是文档不是代码
    # 文档示例不会被 AST 误判为 print 调用（docstring 是 Expr/Constant 节点），
    # 不需要加豁免；但若 grep 工具误判会需要。这里不列，验证 AST 不误报即可。
]


def _iter_py_files(*dirs):
    """递归收集 ``dirs`` 下所有 .py 文件路径（绝对路径）。"""
    out = []
    for d in dirs:
        if os.path.isfile(d) and d.endswith(".py"):
            out.append(d)
            continue
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            # 跳过 __pycache__ / 隐藏目录
            if "__pycache__" in root or os.path.basename(root).startswith("."):
                continue
            for f in files:
                if f.endswith(".py"):
                    out.append(os.path.join(root, f))
    return sorted(out)


def _iter_in_scope():
    """返回 T01-T04 改造范围内（架构师 §7 验收清单）的文件列表。"""
    return [p for p in IN_SCOPE_FILES if os.path.isfile(p)]


def _find_print_calls(py_path):
    """用 AST 找出文件中所有 ``print(...)`` 调用（不含 docstring / 注释）。

    Returns: list[(line, col, func_name)] —— func_name 是 print 本身（永远是 "print"）
    """
    with open(py_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src, filename=py_path)
    except SyntaxError:
        # 解析失败不阻挡测试 —— 让其它用例去发现
        return []

    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # 直接 print(...)
            if isinstance(func, ast.Name) and func.id == "print":
                calls.append((node.lineno, node.col_offset))
            # 也可能是 functools.partial(print, ...) 之类（少见，不展开）
    return calls


def _read_src_lines(py_path):
    """读取源文件，返回行列表（1-based 行号）。"""
    with open(py_path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


def _to_project_rel(abs_path):
    """把绝对路径转成 ``qwen_app/xxx.py`` / ``plugins/xxx.py`` / ``main.py`` 形式。"""
    rel = os.path.relpath(abs_path, PROJ)
    return rel.replace(os.sep, "/")


class TestNoBarePrint(unittest.TestCase):
    """用例 1：业务代码不含裸 print。"""

    def setUp(self):
        self.violations = []  # [(rel_path, line, src_line), ...]
        for py_abs in _iter_in_scope():
            rel = _to_project_rel(py_abs)
            src_lines = _read_src_lines(py_abs)
            for lineno, _col in _find_print_calls(py_abs):
                tag = f"{rel}:{lineno}"
                if tag in ALLOWED_PRINT_LOCATIONS:
                    continue
                # src_line 截前 80 字符方便定位
                src_line = src_lines[lineno - 1].strip()[:80] if lineno <= len(src_lines) else ""
                self.violations.append((rel, lineno, src_line))

    def test_no_bare_print_in_qwen_app_business(self):
        # 输出每条违规方便定位
        if self.violations:
            details = "\n".join(
                f"  - {rel}:{line}  {src_line!r}"
                for rel, line, src_line in self.violations
            )
            self.fail(
                "业务代码不应有裸 print()（必须用 logger）；违规：\n"
                f"{details}\n\n"
                "豁免清单见 ALLOWED_PRINT_LOCATIONS（精确 file:line）。"
            )


class TestMainPyBannersKept(unittest.TestCase):
    """用例 2：main.py 启动横幅（精确字符串）保留为 print。"""

    def test_main_py_banner_prints_kept(self):
        with open(MAIN_PY, "r", encoding="utf-8") as fh:
            src = fh.read()
        # 启动横幅必须仍用 print（不能改成 logger）
        self.assertIn(
            'print("[main] QtQuick UI 已启动"', src,
            "启动横幅 [main] QtQuick UI 已启动 必须保留为 print",
        )
        self.assertIn(
            'bridge.isBusy = {bridge.isBusy}', src,
            "启动横幅 bridge.isBusy 必须保留",
        )
        self.assertIn(
            '当前模型 = {bridge._last_model_name}', src,
            "启动横幅 当前模型 必须保留",
        )


class TestSelftestPrintsKept(unittest.TestCase):
    """用例 3：_run_selftest 内 emit() 的 print(s) 保留。"""

    def test_selftest_prints_kept(self):
        with open(MAIN_PY, "r", encoding="utf-8") as fh:
            src = fh.read()
        # selftest 模式专用：emit() 内部 print(s) 必须保留（运维/CI 抓 stdout）
        self.assertIn("def emit(s=", src, "_run_selftest 必须有 emit() 辅助函数")
        # emit 里 print(s) 这一行在源码中存在（至少一处，未迁移）
        self.assertIn(
            "print(s)",
            src,
            "_run_selftest.emit() 内部 print(s) 必须保留（--selftest 模式 stdout 输出）",
        )


class TestFixturesNotScanned(unittest.TestCase):
    """用例 4：tests/test_*.py 不参与扫描（避免污染）。"""

    def test_test_fixtures_not_scanned(self):
        """重构确认：扫描器只覆盖 T01-T04 范围内的文件，不含 tests/。"""
        # _iter_in_scope() 不会进 tests/
        # 反向断言：随便挑一个 tests/test_*.py 应当不被遍历到
        scanned = _iter_in_scope()
        scanned_rel = {_to_project_rel(p) for p in scanned}
        # 任意选一个测试文件作为样本
        sample = "tests/test_day20_10_10_log.py"
        self.assertNotIn(
            sample, scanned_rel,
            f"{sample} 不应被扫描到（测试 fixture 的 print 不算违规）",
        )
        # 也不应包含架构师 §6 明确排除的范围（chat_window.py / chat_qml_window.py
        # / chat_render.py / worker.py / config.py / scheduler_run.py / qml_demo.py /
        # qml_screenshot.py）—— 这些文件由后续迭代处理。
        excluded = {
            "qwen_app/chat_window.py",
            "qwen_app/chat_qml_window.py",
            "qwen_app/chat_render.py",
            "qwen_app/worker.py",
            "qwen_app/config.py",
            "qwen_app/scheduler_run.py",
            "qwen_app/qml_demo.py",
            "qwen_app/qml_screenshot.py",
        }
        overlap = scanned_rel & excluded
        self.assertEqual(
            overlap, set(),
            f"扫描器不应进入架构师 §6 排除的文件：{overlap}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
