# -*- coding: utf-8 -*-
"""Day 20.6.12 护栏：enable_thinking / enable_tools 是**全局偏好**，不随单条模型保存（P1-COR-1）

来源：tests/artifacts/audit_verification.md P1-COR-1「每模型死复选框」。

事实（复核）：
  settings_dialog._model_edit_form 曾为每条模型提供 chk_think / chk_tools，
  并把结果写进模型字典（enable_thinking / enable_tools）；
  _apply_to_window 在「编辑/删除当前模型」时又把它们覆盖到 window（全局字段）；
  但 chat_window.switch_model **从不读取**模型上的这两个字段（反而会 pop 掉）。
  ⇒ 同一个开关存在两套语义：编辑模型会**静默改写全局偏好**，切换模型则完全不看它。
  属死 UI + 误导，且会丢用户设置。

修复：模型编辑表单不再提供这两个开关（全局偏好的唯一入口是「偏好设置」对话框）；
  _apply_to_window 只同步「连接字段」（URL / Key / Model / Proxy）。

护栏策略 —— 直接调用真实函数并断言返回值 / 副作用，不把期望值抄一遍；
静态检查用 AST 定位函数**可执行体**（跳过 docstring），避免文档字符串里记录
历史写法被误判。
"""
import ast
import os
import sys
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget, QLineEdit  # noqa: E402

from qwen_app import settings_dialog  # noqa: E402
from qwen_app.settings_dialog import _model_edit_form, _apply_to_window  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_SRC = os.path.join(ROOT, "qwen_app", "settings_dialog.py")
CHAT_WINDOW_SRC = os.path.join(ROOT, "qwen_app", "chat_window.py")

BEHAVIOR_KEYS = ("enable_thinking", "enable_tools")


def _func_source(path, name):
    """取函数源码段（自动 dedent，便于对方法单独 ast.parse）。"""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    node = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            node = n
            break
    assert node is not None, f"{name} 未在 {path} 中找到"
    return textwrap.dedent(ast.get_source_segment(src, node) or "")


class _StubWindow:
    """_apply_to_window 只需要这 6 个属性 + 2 个方法。"""

    def __init__(self, thinking, tools):
        self.base_url = ""
        self.api_key = ""
        self.model_id = ""
        self.proxy = ""
        self.enable_thinking = thinking
        self.enable_tools = tools
        self.setup_called = 0
        self.save_called = 0

    def setup_client(self):
        self.setup_called += 1

    def _save_settings(self):
        self.save_called += 1


class TestModelFormDropsBehaviorSwitch(unittest.TestCase):
    """模型编辑表单不得再出现 enable_thinking / enable_tools"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _fill_and_get(self, model):
        parent = QWidget()
        # window 参数在 _model_edit_form 内未被使用，这里传 parent 占位
        form, get_values = _model_edit_form(parent, parent, model)
        url_box = model_box = None
        for le in form.findChildren(QLineEdit):
            ph = le.placeholderText()
            if "openai" in ph:
                url_box = le
            elif "qwen-max" in ph:
                model_box = le
        self.assertIsNotNone(url_box, "未找到 Base URL 输入框（占位符是否改了？）")
        self.assertIsNotNone(model_box, "未找到模型 ID 输入框（占位符是否改了？）")
        url_box.setText("https://example.com/v1")
        model_box.setText("qwen-max")
        with mock.patch.object(settings_dialog.QMessageBox, "warning"):
            return get_values()

    def test_get_values_has_no_behavior_keys(self):
        """即便传入的模型字典**带有**残留开关，也不应把它们带回来"""
        model = {"name": "m", "base_url": "https://x/v1", "api_key": "k",
                 "model_id": "qwen-max", "proxy": "",
                 "enable_thinking": True, "enable_tools": False}
        vals = self._fill_and_get(model)
        self.assertIsNotNone(vals)
        self.assertEqual(set(vals.keys()),
                         {"name", "base_url", "api_key", "model_id", "proxy"})
        for k in BEHAVIOR_KEYS:
            self.assertNotIn(k, vals)

    def test_form_builds_no_checkbox(self):
        src = _func_source(SETTINGS_SRC, "_model_edit_form")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = getattr(f, "id", None) or getattr(f, "attr", None)
                self.assertNotEqual(
                    name, "QCheckBox", "模型编辑表单不应再创建复选框")

    def test_global_prefs_dialog_still_owns_the_switch(self):
        """不能删过头：全局「偏好设置」仍必须提供这两个开关"""
        src = _func_source(SETTINGS_SRC, "show_settings")
        for k in BEHAVIOR_KEYS:
            self.assertIn(k, src)
        self.assertIn("QCheckBox", src)


class TestApplyToWindowKeepsGlobalPrefs(unittest.TestCase):
    """编辑/删除当前模型时，不得顺手改写用户的全局行为偏好"""

    def test_apply_does_not_touch_global_behavior(self):
        w = _StubWindow(thinking=True, tools=True)   # 用户的全局偏好
        # 模型字典里带旧版残留开关（与全局相反），修复后应被忽略
        model = {"base_url": "https://y/v1", "api_key": "k2", "model_id": "m2",
                 "proxy": "http://p",
                 "enable_thinking": False, "enable_tools": False}
        _apply_to_window(w, model)
        # 连接字段应更新
        self.assertEqual(w.base_url, "https://y/v1")
        self.assertEqual(w.api_key, "k2")
        self.assertEqual(w.model_id, "m2")
        self.assertEqual(w.proxy, "http://p")
        # 全局偏好必须**原封不动**
        self.assertTrue(w.enable_thinking, "全局 enable_thinking 被模型残留值覆盖了")
        self.assertTrue(w.enable_tools, "全局 enable_tools 被模型残留值覆盖了")
        self.assertEqual(w.setup_called, 1)
        self.assertEqual(w.save_called, 1)

    def test_apply_source_has_no_behavior_assignment(self):
        src = _func_source(SETTINGS_SRC, "_apply_to_window")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(
                    node.attr, BEHAVIOR_KEYS,
                    f"_apply_to_window 不应读写全局行为开关（发现 .{node.attr}）")


class TestSwitchModelStillCleansLegacy(unittest.TestCase):
    """切换模型时仍应清理注册表里遗留的 per-model 开关字段"""

    def test_switch_model_pops_legacy_fields(self):
        src = _func_source(CHAT_WINDOW_SRC, "switch_model")
        for k in BEHAVIOR_KEYS:
            self.assertIn(k, src, f"switch_model 应清理遗留字段 {k}")
        self.assertIn("pop", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
