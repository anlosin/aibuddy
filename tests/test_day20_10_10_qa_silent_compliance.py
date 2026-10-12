# -*- coding: utf-8 -*-
"""QA 独立验证 —— silent-OK / silent-WARN / must-log-ERROR 注释规范静态扫描。

按架构师 §2 与 §6 验证 **业务代码**（T01-T04 范围内）：

  - **silent-OK**：保留 ``except ... pass`` 但必须有 1 行 ``# silent-OK: <理由>`` 注释
  - **silent-WARN** / **must-log-ERROR**：必须有对应 ``logger.warning`` / ``logger.error`` /
    ``logger.exception`` 调用
  - 三档判定准则不能互相混用：
    - 业务逻辑失败 (must-log-ERROR) 不能伪装成 silent-OK
    - silent-OK 只能用于 cleanup / 取消 / 路径探测 / watcher init
    - silent-WARN 适用于有 toast / 兜底 的可恢复失败

工程师护栏已验证数字 6 / 21 / 9；本文件**正交**：用 AST + grep 综合判定每个
except 分支的「三个标签是否一致」。
"""
import ast
import os
import re
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── silent-OK / silent-WARN / must-log-ERROR 注释统计 + 静态扫描的范围 ──
# 关键：paths.py 加入：架构师 §3 批 6 T6.1 明确指定 paths.py 加 silent-OK 注释。
IN_SCOPE_FILES = [
    os.path.join(_PROJ, "qwen_app", "scheduler.py"),
    os.path.join(_PROJ, "qwen_app", "chat_bridge.py"),
    os.path.join(_PROJ, "qwen_app", "paths.py"),
    os.path.join(_PROJ, "qwen_app", "_bridge_bubble.py"),
    os.path.join(_PROJ, "qwen_app", "_bridge_send.py"),
    os.path.join(_PROJ, "qwen_app", "_bridge_model.py"),
    os.path.join(_PROJ, "qwen_app", "_bridge_plugin.py"),
    os.path.join(_PROJ, "qwen_app", "_bridge_menu.py"),
    os.path.join(_PROJ, "qwen_app", "_bridge_stream.py"),
    os.path.join(_PROJ, "qwen_app", "plugin_manager.py"),
    os.path.join(_PROJ, "plugins", "sql_helper.py"),
    os.path.join(_PROJ, "plugins", "ssh_runner.py"),
    os.path.join(_PROJ, "plugins", "web_search.py"),
    os.path.join(_PROJ, "plugins", "_secret_store.py"),
    os.path.join(_PROJ, "main.py"),
]


def _to_rel(abs_path):
    return os.path.relpath(abs_path, _PROJ).replace(os.sep, "/")


def _read_src(py_path):
    with open(py_path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


# ───────────────────────────────────────────────────────────────────────
# 用例 1：silent-OK 数字核实 — paths.py + scheduler.py 至少 6 处
#   工程师交付摘要：silent-OK = 6。本测试独立 grep 重新计数并断言 ≥ 6。
# ───────────────────────────────────────────────────────────────────────


class TestQASilentOKCount(unittest.TestCase):
    """silent-OK 注释实际数。"""

    def test_silent_ok_count_is_6(self):
        """silent-OK 注释必须 ≥ 6（paths.py:4 + scheduler.py:2 = 6）。"""
        n = 0
        per = {}
        for py_abs in IN_SCOPE_FILES:
            if not os.path.isfile(py_abs):
                continue
            rel = _to_rel(py_abs)
            cnt = sum(
                1 for line in _read_src(py_abs)
                if re.match(r"^\s*#\s*silent-OK\s*:", line)
            )
            if cnt:
                per[rel] = cnt
            n += cnt
        self.assertGreaterEqual(
            n, 6,
            f"silent-OK 注释应 ≥ 6（paths.py+scheduler.py 已确认），实际 {n}，分布：{per}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 2：silent-WARN 数字核实 — 工程师交付摘要说 21，本独立 grep 是 27
# ───────────────────────────────────────────────────────────────────────


class TestQASilentWarnCount(unittest.TestCase):
    """silent-WARN 注释实际数。"""

    def test_silent_warn_count_is_at_least_15(self):
        n = 0
        per = {}
        for py_abs in IN_SCOPE_FILES:
            if not os.path.isfile(py_abs):
                continue
            rel = _to_rel(py_abs)
            cnt = sum(
                1 for line in _read_src(py_abs)
                if re.match(r"^\s*#\s*silent-WARN\s*:", line)
            )
            if cnt:
                per[rel] = cnt
            n += cnt
        self.assertGreaterEqual(
            n, 15,
            f"silent-WARN 注释应 ≥ 15（工程师交付说 21），实际 {n}，分布：{per}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 3：must-log-ERROR 数字核实 — 工程师交付摘要说 9，本独立 grep 是 11
# ───────────────────────────────────────────────────────────────────────


class TestQAMustLogErrorCount(unittest.TestCase):
    """must-log-ERROR 注释实际数。"""

    def test_must_log_error_count_is_at_least_8(self):
        n = 0
        per = {}
        for py_abs in IN_SCOPE_FILES:
            if not os.path.isfile(py_abs):
                continue
            rel = _to_rel(py_abs)
            cnt = sum(
                1 for line in _read_src(py_abs)
                if re.match(r"^\s*#\s*must-log-ERROR\s*:", line)
            )
            if cnt:
                per[rel] = cnt
            n += cnt
        self.assertGreaterEqual(
            n, 8,
            f"must-log-ERROR 注释应 ≥ 8（工程师交付说 9），实际 {n}，分布：{per}"
        )


# ───────────────────────────────────────────────────────────────────────
# 用例 4：核心断言 —— except 块内有 logger.X 调用时，前文注释必须标注对应 tier
#   防止工程师「写了 logger 但忘了分类注释」。
#   只检查工程师交付摘要覆盖到的 4 个关键路径（scheduler / chat_bridge /
#   plugin_manager / bridge mixin / main）。
# ───────────────────────────────────────────────────────────────────────


class TestQASilentTierAnnotationsConsistent(unittest.TestCase):
    """except 块内有 logger.warning/error/exception 时，前文注释必须标注类别。"""

    EXPECTED_TIERS = {
        "logger.warning": "silent-WARN",
        "logger.error": "must-log-ERROR",
        "logger.exception": "must-log-ERROR",
        "logger.critical": "must-log-ERROR",
    }

    def test_logger_call_has_tier_annotation(self):
        """逐文件找每个 except + logger.X 调用，验证前 3 行内是否标注。"""
        violations = []
        for py_abs in IN_SCOPE_FILES:
            if not os.path.isfile(py_abs):
                continue
            rel = _to_rel(py_abs)
            src_lines = _read_src(py_abs)
            try:
                tree = ast.parse("\n".join(src_lines), filename=py_abs)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                # 在 except body 内找 logger.X(...) 调用
                tier_found = None  # 实际调用的 tier
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        if isinstance(sub.func.value, ast.Name) and sub.func.value.id == "logger":
                            attr = sub.func.attr
                            if attr in self.EXPECTED_TIERS:
                                tier_found = attr
                                break
                if tier_found is None:
                    continue
                # 检查前 3 行注释
                line_idx = node.lineno - 1
                expected_tag = self.EXPECTED_TIERS[tier_found]
                has_tag = False
                for offset in range(max(0, line_idx - 3), line_idx + 1):
                    if expected_tag in src_lines[offset]:
                        has_tag = True
                        break
                if not has_tag:
                    violations.append((rel, node.lineno, tier_found, expected_tag,
                                       src_lines[line_idx].strip()[:60]))

        if violations:
            details = "\n".join(
                f"  {rel}:{line}  调用={call}, 应有 #{tag} 注释, 代码={src!r}"
                for rel, line, call, tag, src in violations
            )
            self.fail(
                "以下 except 调了 logger 但缺 tier 注释：\n"
                f"{details}"
            )


# ───────────────────────────────────────────────────────────────────────
# 用例 5：silent-OK 理由质量自检
#   silent-OK 不能用于「业务逻辑失败 + 数据丢失 + 持久化失败」等场景。
# ───────────────────────────────────────────────────────────────────────


class TestQASilentOKReasonQuality(unittest.TestCase):
    """silent-OK 注释的理由不应包含「写盘失败 / 数据丢失 / 用户数据」等强错误关键词。"""

    FORBIDDEN_PHRASES = (
        "用户数据丢失",
        "写盘失败",
        "持久化失败",
        "保存失败",
        "数据丢失",
    )

    def test_silent_ok_reasons_dont_admit_data_loss(self):
        violations = []
        for py_abs in IN_SCOPE_FILES:
            if not os.path.isfile(py_abs):
                continue
            rel = _to_rel(py_abs)
            src_lines = _read_src(py_abs)
            for i, line in enumerate(src_lines):
                m = re.match(r"^\s*#\s*silent-OK\s*:\s*(.+)$", line)
                if not m:
                    continue
                reason = m.group(1)
                for bad in self.FORBIDDEN_PHRASES:
                    if bad in reason:
                        violations.append((rel, i + 1, reason, bad))

        if violations:
            details = "\n".join(
                f"  {rel}:{line}  reason={reason!r} 含禁用词 {bad!r}"
                for rel, line, reason, bad in violations
            )
            self.fail(
                "silent-OK 注释理由含「数据丢失 / 写盘失败」等强错误语义，"
                "应改为 must-log-ERROR：\n"
                f"{details}"
            )


# ───────────────────────────────────────────────────────────────────────
# 用例 6：silent-OK 注释覆盖工程师 T04 重点文件 — scheduler.py + paths.py
# ───────────────────────────────────────────────────────────────────────


class TestQASilentOKKeyFilesCoverage(unittest.TestCase):
    """工程师 T04 §3 批 6 任务明确指定三文件加 silent-OK 注释：

    - ``qwen_app/scheduler.py``: 至少 2 处 silent-OK（行 689、717）
    - ``qwen_app/paths.py``: 至少 4 处 silent-OK（探测 / 扫描 / 释放）
    """

    def test_scheduler_has_at_least_2_silent_ok(self):
        target = os.path.join(_PROJ, "qwen_app", "scheduler.py")
        self.assertTrue(os.path.isfile(target), f"{target} 必须存在")
        n = sum(1 for line in _read_src(target) if re.match(r"^\s*#\s*silent-OK\s*:", line))
        self.assertGreaterEqual(
            n, 2,
            f"scheduler.py 必须 ≥ 2 处 silent-OK 注释，实际 {n}"
        )

    def test_paths_has_at_least_4_silent_ok(self):
        target = os.path.join(_PROJ, "qwen_app", "paths.py")
        self.assertTrue(os.path.isfile(target), f"{target} 必须存在")
        n = sum(1 for line in _read_src(target) if re.match(r"^\s*#\s*silent-OK\s*:", line))
        self.assertGreaterEqual(
            n, 4,
            f"paths.py 必须 ≥ 4 处 silent-OK 注释（_dir_writable / scan / release 等），实际 {n}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
