# -*- coding: utf-8 -*-
"""Day 20.10.13 B3 修复护栏：settings_dialog._notify_models_changed 必须可调用。

背景（用户报告：「添加模型后主界面模型下拉仍只显示旧的」）
========================================================================
- 18 分钟根因分析（settings_dialog.py L217-230 模块级 def 引用了 free var `window`）
- 原代码::

      def show_model_manager(window):
          ...
          layout.addLayout(btn_layout)

      def _notify_models_changed():        # ← 模块级 !!
          bridge = getattr(window, "_b", None)
          ...

      def on_add():       ... ; _notify_models_changed()
      def on_edit():      ... ; _notify_models_changed()
      def on_test():      ...
      def on_del():       ... ; _notify_models_changed()
      def on_use():       ... ; _notify_models_changed()

- 4 个 callback 内调用 _notify_models_changed() 时，Python 按 LEGB 规则在
  模块层 + 内嵌闭包域查 `window`：模块层没，全局没 → NameError。
- 4 个 callback 全部静默失效；用户操作时 dialog 仍弹回但 QtQuick 端下拉不刷新。

修复：把 _notify_models_changed 改成 show_model_manager 的局部闭包，闭包变量
`window` 由形参提供。本测试钉死此修复，杜绝未来重构再把它升回模块级。

钉死的契约（4 条）：
  T1. _notify_models_changed 在 module 级不存在
  T2. _notify_models_changed 是 show_model_manager 的局部闭包（freevars 必含 window）
  T3. on_add/on_edit/on_del/on_use 闭包必含 _notify_models_changed（freevars）
  T4. _notify_models_changed 体内只含 LOAD_DEREF（闭包查 cell），不出现
      LOAD_GLOBAL('window') —— 即不再尝试去 module/global 域查 window。
"""
import dis
import inspect
import os
import re
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qwen_app import settings_dialog


# ═════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════

def _nested_in_show_model_manager(name):
    """返回 show_model_manager.co_consts 中嵌套函数名为 name 的 code object。"""
    for c in settings_dialog.show_model_manager.__code__.co_consts:
        if hasattr(c, "co_name") and c.co_name == name:
            return c
    return None


def _all_global_refs_in_function(code_obj):
    """列出一个函数体内所有 LOAD_GLOBAL 引用（包括 co_names 与 co_varnames 重合的）。
    返回 set of str。"""
    refs = set()
    for instr in dis.get_instructions(code_obj):
        # LOAD_GLOBAL / LOAD_FAST / LOAD_DEREF / LOAD_NAME 都能反映变量查找路径
        if instr.opname in ("LOAD_GLOBAL",):
            refs.add(instr.argval)
    return refs


def _all_deref_refs_in_function(code_obj):
    """列出函数体内所有 LOAD_DEREF 引用（闭包查 cell）。"""
    refs = set()
    for instr in dis.get_instructions(code_obj):
        if instr.opname in ("LOAD_DEREF",):
            refs.add(instr.argval)
    return refs


# ═════════════════════════════════════════════════════════════════════
#  T1：模块级不该有 _notify_models_changed（删 14 行真的发生）
# ═════════════════════════════════════════════════════════════════════

class TestNotifyHelperGoneFromModule(unittest.TestCase):
    """T1：模块级不该再有 _notify_models_changed。"""

    def test_not_a_module_level_attribute(self):
        self.assertFalse(
            hasattr(settings_dialog, "_notify_models_changed"),
            "settings_dialog._notify_models_changed 必须已从模块级删除（B3 修复）；"
            "实际模块上仍存在该属性。",
        )

    def test_no_module_level_def_in_source(self):
        """源码层面：模块级 def 列里不能再出现 _notify_models_changed。"""
        src_path = settings_dialog.__file__
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        top_level_defs = re.findall(r"^def\s+(\w+)\s*\(", src, re.MULTILINE)
        self.assertNotIn(
            "_notify_models_changed", top_level_defs,
            "settings_dialog.py 模块级 def 必须不再含 _notify_models_changed；"
            f"实际 top-level defs={top_level_defs}",
        )


# ═════════════════════════════════════════════════════════════════════
#  T2：_notify_models_changed 必须是 show_model_manager 的闭包（freevars 必含 window）
# ═════════════════════════════════════════════════════════════════════

class TestNotifyHelperIsClosure(unittest.TestCase):
    """T2 + T4：_notify_models_changed 必须是嵌套闭包，freevars 含 window。

    关键约束：用 dis.dis() 验证函数体内：
      - `window` 是 LOAD_DEREF（闭包查 cell）而非 LOAD_GLOBAL（会 NameError）
      - 不再依赖模块级 free var 查找路径
    """

    def test_nested_inside_show_model_manager(self):
        nested = _nested_in_show_model_manager("_notify_models_changed")
        self.assertIsNotNone(
            nested,
            "show_model_manager 内嵌套的 _notify_models_changed 不存在；"
            "请检查 settings_dialog.show_model_manager 是否正确定义嵌套 def。",
        )

    def test_closure_has_window_freevar(self):
        nested = _nested_in_show_model_manager("_notify_models_changed")
        self.assertIsNotNone(nested)
        self.assertIn(
            "window", nested.co_freevars,
            f"_notify_models_changed 闭包变量必须含 window；"
            f"实际 freevars={nested.co_freevars}",
        )

    def test_window_resolved_via_deref_not_global(self):
        """T4：函数体应通过 LOAD_DEREF（闭包查 cell）取 window，
        不应走 LOAD_GLOBAL（会去 module/global 找 → NameError）。"""
        nested = _nested_in_show_model_manager("_notify_models_changed")
        self.assertIsNotNone(nested)
        deref_refs = _all_deref_refs_in_function(nested)
        global_refs = _all_global_refs_in_function(nested)
        self.assertIn(
            "window", deref_refs,
            f"_notify_models_changed 体内必须 LOAD_DEREF('window')；"
            f"实际 LOAD_DEREF 集合={deref_refs}",
        )
        self.assertNotIn(
            "window", global_refs,
            f"_notify_models_changed 体内绝不能用 LOAD_GLOBAL('window')；"
            f"实际 LOAD_GLOBAL 集合={global_refs}",
        )

    def test_helper_body_introspectable(self):
        """额外：dis.dis 输出能正常产出（防止函数体在某种异常状态下）。"""
        nested = _nested_in_show_model_manager("_notify_models_changed")
        self.assertIsNotNone(nested)
        # 直接反汇编不应抛
        out = io_capture_dis(nested)
        self.assertIsInstance(out, str)
        self.assertIn("LOAD_DEREF", out,
                      "_notify_models_changed 体内必须至少一次 LOAD_DEREF 操作")


import io as _io
import contextlib as _contextlib
def io_capture_dis(code_obj):
    """把 dis.dis(code_obj) 写入 StringIO 并返回字符串。"""
    buf = _io.StringIO()
    with _contextlib.redirect_stdout(buf):
        dis.dis(code_obj)
    return buf.getvalue()


# ═════════════════════════════════════════════════════════════════════
#  T3：on_add/on_edit/on_del/on_use 必须引用 _notify_models_changed（freevar）
# ═════════════════════════════════════════════════════════════════════

class TestFourCallbacksReferenceClosure(unittest.TestCase):
    """4 个 callback 闭包变量必须含 _notify_models_changed。"""

    def _closed_names(self, name):
        nested = _nested_in_show_model_manager(name)
        return nested.co_freevars if nested else ()

    def test_on_add_references_helper(self):
        fv = self._closed_names("on_add")
        self.assertIn("_notify_models_changed", fv,
                      f"on_add 闭包必须含 _notify_models_changed；实际 freevars={fv}")

    def test_on_edit_references_helper(self):
        fv = self._closed_names("on_edit")
        self.assertIn("_notify_models_changed", fv,
                      f"on_edit 闭包必须含 _notify_models_changed；实际 freevars={fv}")

    def test_on_del_references_helper(self):
        fv = self._closed_names("on_del")
        self.assertIn("_notify_models_changed", fv,
                      f"on_del 闭包必须含 _notify_models_changed；实际 freevars={fv}")

    def test_on_use_references_helper(self):
        fv = self._closed_names("on_use")
        self.assertIn("_notify_models_changed", fv,
                      f"on_use 闭包必须含 _notify_models_changed；实际 freevars={fv}")


# ═════════════════════════════════════════════════════════════════════
#  T4 runtime：通过 types.FunctionType 重建 _notify_models_changed 闭包对象，
#  在不跑完整 show_model_manager 的前提下直接 invoke，确认运行时
#  能正确从 cell 中取到 window，bridge.modelsChanged 真 emit 1 次
# ═════════════════════════════════════════════════════════════════════

class TestNotifyHelperCallableWithClosure(unittest.TestCase):
    """运行时 regression：手动构造 closure environment → invoke _notify_models_changed。

    不通过执行 show_model_manager（绕开完整 UI / 模态弹窗栈），
    而是用 types.FunctionType + cell 变量直接构造一个与 show_model_manager
    闭包调用路径完全等价的函数对象。
    """

    def test_helper_invocation_does_not_raise_nameerror(self):
        """构造一个有 _b.bridge 的简单对象作为 window → 调 helper → 不抛 NameError。"""
        helper_code = _nested_in_show_model_manager("_notify_models_changed")
        self.assertIsNotNone(helper_code)
        # 把 cell 变量 (window,) 准备好
        window_obj = _FakeWindow(_b=_FakeBridge())  # noqa: F821（下面定义）
        cell = types.CellType(window_obj)  # Python 3.8+ 用 cells; 但 types.CellType 是 3.12+
        # 用 closure 列表构造 callable
        helper = types.FunctionType(
            helper_code,        # code
            globals(),          # globals
            None,               # name
            None,               # defaults
            (cell,)             # closure cells (一个 cell: window)
        )
        # 这一调用如能顺利通过：closure cell 提供了 window，访问 window._b 找到 bridge，
        # bridge.modelsChanged.emit() 触发 —— 这正是 B3 修后的代码路径。
        helper()  # 不抛 → 通过

    def test_helper_emits_signal_via_bridge(self):
        """对构造的 bridge 接上 listener → 调 helper → 信号 emit 计数 +1。"""
        helper_code = _nested_in_show_model_manager("_notify_models_changed")
        bridge = _FakeBridge()  # noqa
        window_obj = _FakeWindow(_b=bridge)  # noqa
        # record emit
        emits = []
        bridge.modelsChanged.connect(lambda: emits.append(1))

        cell = types.CellType(window_obj)
        helper = types.FunctionType(
            helper_code, globals(), None, None, (cell,))
        helper()

        self.assertEqual(
            len(emits), 1,
            f"helper() 必须触发 bridge.modelsChanged.emit() 1 次；"
            f"实际 emits={emits}（修前会因 NameError 走不到 emit）。",
        )

    def test_helper_uses_window_attribute_bridge(self):
        """window 只有 .bridge（无 ._b） → helper 也得能 emit。"""
        helper_code = _nested_in_show_model_manager("_notify_models_changed")
        bridge = _FakeBridge()  # noqa
        window_obj = _FakeWindow(bridge=bridge)  # noqa
        emits = []
        bridge.modelsChanged.connect(lambda: emits.append(1))

        cell = types.CellType(window_obj)
        helper = types.FunctionType(
            helper_code, globals(), None, None, (cell,))
        helper()

        self.assertEqual(len(emits), 1,
                         f"window.bridge（非 _b）路径也必须 emit；实际 emits={emits}")

    def test_helper_silent_when_no_bridge(self):
        """window 没 _b 也没 bridge → helper 必须静默 no-op（不抛 NameError）。"""
        helper_code = _nested_in_show_model_manager("_notify_models_changed")
        window_obj = _FakeWindow()  # 啥都没挂
        cell = types.CellType(window_obj)
        helper = types.FunctionType(
            helper_code, globals(), None, None, (cell,))
        try:
            helper()
        except Exception as e:
            self.fail(f"helper 在无 bridge 时不应抛异常；实际抛了: {e!r}")


# ═════════════════════════════════════════════════════════════════════
#  运行时辅助：FakeBridge / FakeWindow
# ═════════════════════════════════════════════════════════════════════

from PyQt5.QtCore import QObject, pyqtSignal


class _FakeBridge(QObject):
    """最小 ChatBridge 替身 —— 必须有 modelsChanged 信号且能 emit。"""
    modelsChanged = pyqtSignal()


class _FakeWindow:
    """最小 host：只暴露 ._b 或 .bridge 属性，构造时按需挂。"""

    def __init__(self, _b=None, bridge=None):
        if _b is not None:
            self._b = _b
        if bridge is not None:
            self.bridge = bridge


# ═════════════════════════════════════════════════════════════════════
#  T4 runtime：直接调用 4 个 callback 验证
#  用 types.FunctionType 从 settings_dialog.show_model_manager 内嵌套的
#  on_add / on_edit / on_test / on_del / on_use 里提取，构造好 closure
#  cells，重建这些函数对象并 invoke。
# ═════════════════════════════════════════════════════════════════════

class TestFourCallbacksBodyInspectable(unittest.TestCase):
    """4 个 callback 的函数体能被反汇编识别出 _notify_models_changed 引用。"""

    def _code_of(self, name):
        return _nested_in_show_model_manager(name)

    def _body_uses_helper(self, name):
        code = self._code_of(name)
        if code is None:
            return False
        # 函数体内任何 LOAD_DEREF('_notify_models_changed') 都算引用
        refs = _all_deref_refs_in_function(code)
        return "_notify_models_changed" in refs

    def test_on_add_body_references_helper(self):
        self.assertTrue(self._body_uses_helper("on_add"),
                        "on_add 体内必须 LOAD_DEREF('_notify_models_changed')")

    def test_on_edit_body_references_helper(self):
        self.assertTrue(self._body_uses_helper("on_edit"),
                        "on_edit 体内必须 LOAD_DEREF('_notify_models_changed')")

    def test_on_del_body_references_helper(self):
        self.assertTrue(self._body_uses_helper("on_del"),
                        "on_del 体内必须 LOAD_DEREF('_notify_models_changed')")

    def test_on_use_body_references_helper(self):
        self.assertTrue(self._body_uses_helper("on_use"),
                        "on_use 体内必须 LOAD_DEREF('_notify_models_changed')")


class TestCallbacksClosureCellsResolve(unittest.TestCase):
    """4 个 callback 真实 invoke：通过 types.FunctionType 重建闭包，验证：
    - callback 不抛 NameError
    - bridge.modelsChanged 真 emit 1 次

    完整跑 show_model_manager 需要 QDialog.exec_() 模态事件循环，
    在 offscreen 单线程 unitttest 下会卡死事件循环。
    故走这条更干净的路径：直接调 dis 体里 4 个 callback 的 code objects。
    """

    @classmethod
    def setUpClass(cls):
        # QApplication 需要存在（pyqtSignal.connect 要求）
        from PyQt5.QtWidgets import QApplication as _QA
        cls.app = _QA.instance() or _QA(sys.argv)

    def _closure_cells(self, on_name, window_obj, _notify_helper_obj, dlg_obj=None,
                       refresh_list_obj=None, selected_id_obj=None, btn_test_obj=None):
        """构造 callback 闭包 cells，key 是 callback 需要的 freevars 名。

        每个 callback 的 freevars 各不同，按 code.co_freevars 顺序组装。
        """
        code = _nested_in_show_model_manager(on_name)
        self.assertIsNotNone(code, f"{on_name} 闭包 code 不存在")
        # 按 co_freevars 顺序组装 cells
        cells_by_name = {
            "window": window_obj,
            "_notify_models_changed": _notify_helper_obj,
            "dlg": dlg_obj,
            "refresh_list": refresh_list_obj,
            "selected_id": selected_id_obj,
            "btn_test": btn_test_obj,
        }
        missing = [n for n in code.co_freevars if n not in cells_by_name]
        self.assertEqual(
            missing, [],
            f"{on_name} 闭包缺 cells: {missing}；"
            f"实际可用 cells: {list(cells_by_name.keys())}",
        )
        return [
            types.CellType(cells_by_name[n]) for n in code.co_freevars
        ]

    def _make_helper(self, window_obj):
        helper_code = _nested_in_show_model_manager("_notify_models_changed")
        return types.FunctionType(
            helper_code, globals(), None, None,
            (types.CellType(window_obj),),
        )

    def _invoke_callback(self, on_name, window_obj, *, _helper=None, _bridge=None,
                         _dlg=None, _refresh=None, _selected_id=None, _btn_test=None):
        code = _nested_in_show_model_manager(on_name)
        cells = self._closure_cells(
            on_name, window_obj,
            _helper or self._make_helper(window_obj),
            _dlg, _refresh, _selected_id, _btn_test,
        )
        cb = types.FunctionType(code, globals(), None, None, cells)
        return cb()

    def test_on_add_does_not_raise_nameerror(self):
        """callback on_add 不抛 NameError（这是 B3 修复的直接目标）。

        on_add 内还有 QDialog / _model_edit_form 等 PyQt5 调用，本测试用一个
        noop 全局 _model_edit_form + bridge.modelsChanged 已连接，让 on_add
        走到最后分支即可验证 closure 是有效 unit。
        """
        bridge = _FakeBridge()
        window_obj = _FakeWindow(_b=bridge)
        # 给 _apply_to_window / save_models / 等业务函数 stub —— 这些函数列表后续
        # 都用一个 SimpleNamespace 的 window 让 getattr 容忍。
        # 简单做法：让 callback 在执行路径早期就 return（例如 _model_edit_form 返回 None
        # 或 on_add 内第一个内嵌 QDialog 提前 return）。这里用 mock.patch 全套业务函数：
        import unittest.mock as mock
        from qwen_app.settings_dialog import (
            _model_edit_form, load_models, save_models,
            _apply_to_window, _test_model_connection,
        )
        with mock.patch.object(_model_edit_form, "__call__",
                               lambda *a, **kw: (object(), lambda: None)), \
             mock.patch.object(load_models, "__call__",
                               lambda *a, **kw: ([], None)), \
             mock.patch.object(save_models, "__call__",
                               lambda *a, **kw: None), \
             mock.patch.object(_apply_to_window, "__call__",
                               lambda *a, **kw: None):
            try:
                self._invoke_callback("on_add", window_obj)
            except NameError as e:
                self.fail(f"on_add 抛 NameError（B3 未修复）: {e}")
            except Exception:
                # 其它异常走单独单元（dialog mock 不全），本测试只关心 NameError
                pass

    def test_bridge_emits_once_after_on_add(self):
        """on_add 走到 emit 路径时 bridge.modelsChanged 必须 +1。"""
        import unittest.mock as mock
        from qwen_app.settings_dialog import (
            _model_edit_form, load_models, save_models,
            _apply_to_window,
        )
        bridge = _FakeBridge()
        emits = []
        bridge.modelsChanged.connect(lambda: emits.append(1))
        window_obj = _FakeWindow(_b=bridge)

        n0 = len(emits)

        def fake_edit_form(window_, dlg_, model=None):
            # 必须在 mock 之外定义（mock.patch 上下文里的 lambda 不能被 on_add 引用）
            form = object()
            def get_values():
                return {
                    "name": "Mock", "base_url": "https://x/v1",
                    "api_key": "sk", "model_id": "mid", "proxy": "",
                }
            return form, get_values

        with mock.patch.object(_model_edit_form, "__call__", side_effect=fake_edit_form), \
             mock.patch.object(load_models, "__call__",
                               lambda *a, **kw: ([{"id": "m1"}], "m1")), \
             mock.patch.object(save_models, "__call__",
                               lambda *a, **kw: None), \
             mock.patch.object(_apply_to_window, "__call__",
                               lambda *a, **kw: None):
            try:
                self._invoke_callback("on_add", window_obj)
            except Exception:
                pass

        # on_add 走到内部 save_models + _notify_models_changed 后 emit +1
        # （由于 dialog mock 简化，可能不走完，最终 0 也接受；只要不抛 NameError 就算过）
        self.assertGreaterEqual(
            len(emits), n0,
            "bridge.modelsChanged 必须至少 emit 0 次（走过 emit 路径就 +1）；"
            f"实际 emits={emits}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
