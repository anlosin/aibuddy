# -*- coding: utf-8 -*-
"""Day 20.6.12 护栏：Office/PDF 插件路径边界必须与 write_file 一致（P0-SEC-3）

来源：Day 20.6.10 审计 → Day 20.6.11 复核（tests/artifacts/audit_verification.md P0-SEC-3）。

事实（复核确认）：
  docx.py / excel.py / pptx.py 的 `_safe_path` 开头有
  `if os.path.isabs(filepath): return filepath` → 绝对路径**直接放行**，
  绕过 workspace 边界；而三处 docstring 都写着「与 write_file 保持一致」。
  write_file.py 并无该短路（os.path.join 遇绝对路径丢前缀 → 越界 → ValueError）。
  复核另发现 pdf.py 存在同一份代码（审计漏报），但它是**零调用死代码**。

修复：docx/excel/pptx 的写路径对齐 write_file；pdf.py 删除死代码。

护栏策略 —— **不重复实现期望值**，而是把 Office 的 `_safe_path` 与
write_file._safe_path 在多个样本上**对拍**：行为完全一致才算通过。
这样将来任何一边漂移都会被测到，也不会把 workspace 具体位置写死在测试里。
静态检查用 AST 精确定位函数**可执行体**（跳过 docstring），
避免「docstring 里记录了历史写法」被误判成短路还在。
"""
import ast
import os
import sys
import unittest
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(ROOT, "plugins")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 越界样本：项目根的上一级，必然在 workspace 之外
OUTSIDE = os.path.abspath(os.path.join(ROOT, os.pardir, "evil.docx"))

SAMPLES = [
    "report.docx",                       # 普通文件名
    "sub/report.docx",                   # 相对子目录
    "sub\\report.docx",                  # Windows 分隔符
    "../escape.docx",                    # 上跳穿越
    "..\\escape.docx",                   # 上跳穿越（Windows 分隔符）
    "a/b/../../c.docx",                  # 中途上跳
    OUTSIDE,                             # 绝对路径（项目外）
    os.path.join(ROOT, "inside.docx"),   # 绝对路径（项目内）
    "C:\\Windows\\Temp\\evil.docx",      # 绝对路径（系统盘）
    "/tmp/evil.docx",                    # 绝对路径（POSIX 风格）
    "",                                  # 空串
]


def _load(plugin_name):
    full = os.path.join(PLUGINS_DIR, plugin_name + ".py")
    spec = importlib.util.spec_from_file_location("guard_" + plugin_name, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _outcome(fn, arg):
    """把调用结果归一为可比较形式：(True, 路径) 或 (False, 异常类型名)"""
    try:
        return (True, fn(arg))
    except Exception as e:                # noqa: BLE001 — 故意抓全部，比较异常类型
        return (False, type(e).__name__)


def _find_function(path, func_name):
    """返回该文件顶层同名函数的 AST 节点，找不到返回 None"""
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def _code_uses_attr(fn_node, attr_name):
    """在函数**可执行体**（跳过 docstring）里查找对 X.<attr_name> 的引用

    只扫可执行语句，不会被 docstring 里记录的历史写法误触发。
    """
    body = list(fn_node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant):
        body = body[1:]                  # 跳过 docstring
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Attribute) and n.attr == attr_name:
                return True
    return False


class TestOfficePathParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = _load("write_file")
        cls.mods = {n: _load(n) for n in ("docx", "excel", "pptx", "pdf")}

    # ── 核心：与 write_file 对拍 ──
    def test_parity_with_write_file(self):
        """docx/excel/pptx 的 _safe_path 与 write_file 逐一对照，结果必须完全相同"""
        for name in ("docx", "excel", "pptx"):
            fn = getattr(self.mods[name], "_safe_path", None)
            self.assertTrue(callable(fn), f"{name} 缺少 _safe_path")
            for s in SAMPLES:
                with self.subTest(plugin=name, sample=s):
                    self.assertEqual(
                        _outcome(fn, s), _outcome(self.wf._safe_path, s),
                        f"{name}._safe_path({s!r}) 与 write_file 行为不一致",
                    )

    def test_absolute_outside_is_rejected(self):
        """绝对路径（项目外）必须被拒绝 —— 这正是本次修复的核心"""
        for name in ("docx", "excel", "pptx"):
            with self.subTest(plugin=name):
                ok, detail = _outcome(self.mods[name]._safe_path, OUTSIDE)
                self.assertFalse(ok, f"{name} 放行了越界绝对路径: {detail}")
                self.assertEqual(detail, "ValueError",
                                 f"{name} 应以 ValueError 拒绝越界路径，实际 {detail}")

    def test_traversal_is_rejected(self):
        """../ 穿越必须被拒绝

        注意 `a/b/../../c.docx` 规范化后等于 `c.docx`，**仍在根内** —— 它不是越界，
        见 test_mid_traversal_staying_inside_is_allowed。
        """
        for name in ("docx", "excel", "pptx"):
            for s in ("../escape.docx", "..\\escape.docx"):
                with self.subTest(plugin=name, sample=s):
                    ok, detail = _outcome(self.mods[name]._safe_path, s)
                    self.assertFalse(ok, f"{name} 放行了穿越路径 {s!r}: {detail}")

    def test_mid_traversal_staying_inside_is_allowed(self):
        """上跳后仍落在根内的路径不属于穿越，应与 write_file 一样放行（不误杀正常用法）"""
        for name in ("docx", "excel", "pptx"):
            with self.subTest(plugin=name):
                ok, val = _outcome(self.mods[name]._safe_path, "a/b/../../c.docx")
                self.assertTrue(ok, f"{name} 误拒了未越界的路径: {val}")
                self.assertTrue(str(val).endswith("c.docx"))

    def test_normal_relative_still_works(self):
        """正常用法不能被误伤：相对路径要能解析成路径且不抛异常"""
        for name in ("docx", "excel", "pptx"):
            with self.subTest(plugin=name):
                ok, val = _outcome(self.mods[name]._safe_path, "sub/report.docx")
                self.assertTrue(ok, f"{name} 误拒正常相对路径: {val}")
                self.assertTrue(str(val).endswith("report.docx"))

    # ── 静态：短路不得再出现（AST 精确定位可执行体） ──
    def test_no_isabs_shortcut_in_write_plugins(self):
        """写路径实现的可执行体里不得再有 isabs 短路（防止有人改回去）"""
        for name in ("docx", "excel", "pptx"):
            with self.subTest(plugin=name):
                node = _find_function(os.path.join(PLUGINS_DIR, name + ".py"),
                                      "_safe_path")
                self.assertIsNotNone(node, f"{name} 找不到 _safe_path 定义")
                self.assertFalse(
                    _code_uses_attr(node, "isabs"),
                    f"{name}._safe_path 又出现了 isabs 短路（绝对路径绕界风险）",
                )

    def test_read_path_still_allows_absolute(self):
        """读取路径是有意放宽的，不得被本次收紧误伤（绝对路径原样返回）"""
        for name in ("docx", "excel", "pptx", "pdf"):
            fn = getattr(self.mods[name], "_resolve_read_path", None)
            self.assertTrue(callable(fn), f"{name} 缺少 _resolve_read_path")
            with self.subTest(plugin=name):
                self.assertEqual(fn(OUTSIDE), OUTSIDE,
                                 f"{name}._resolve_read_path 不应改写绝对路径")

    # ── pdf：死代码已清理 ──
    def test_pdf_has_no_write_path_helper(self):
        """pdf 插件是只读的，其零调用 _safe_path 死代码应已删除"""
        self.assertFalse(hasattr(self.mods["pdf"], "_safe_path"),
                         "pdf.py 的 _safe_path 死代码应已删除")
        self.assertIsNone(
            _find_function(os.path.join(PLUGINS_DIR, "pdf.py"), "_safe_path"),
            "pdf.py 仍定义了 _safe_path（死代码未清理）",
        )

    # ── 端到端：工具入口不会真的写出越界文件 ──
    def test_create_docx_rejects_absolute_end_to_end(self):
        """端到端：create_docx 传绝对越界路径 → 返回错误串，且磁盘上没多出文件"""
        try:
            import docx  # noqa: F401
        except ImportError:
            self.skipTest("未安装 python-docx")
        if os.path.exists(OUTSIDE):
            os.remove(OUTSIDE)
        r = self.mods["docx"].execute("create_docx", {
            "filename": OUTSIDE, "title": "t", "sections": []})
        self.assertIn("错误", r)
        self.assertFalse(os.path.exists(OUTSIDE), f"越界文件被创建了: {OUTSIDE}")


if __name__ == "__main__":
    unittest.main()
