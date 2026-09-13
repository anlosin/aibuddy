"""Day 19.1 护栏测试：所有 PyQt QML 信号必须 ≤2 参数（QTBUG-94360 防护）。

Qt 5.15.2 已知缺陷：QML Connections 连接 ≥3 参数 pyqtSignal 会栈越界
崩溃（0xC0000005 / 0xC0000409）。pyqtProperty 的 notify= 也必须无参。

每个新加的 pyqtSignal(参数 ≥3) 都会让 QML 启动崩溃概率陡增。
本测试扫描 chat_bridge.py 全部信号定义，确保没有任何 QML-可见
信号违反规则。

white list：worker.py 的 tool_call_result（3 参数）是 Python-to-Python
内部信号，不被 QML 监听，可以保留。chat_bridge.py 的所有信号必须
合规（包括 toolCallResult，它面向 QML）。
"""
import ast
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestChatBridgeSignalsQmlSafe(unittest.TestCase):
    """chat_bridge.py 所有 pyqtSignal 必须 ≤2 参数（防 QTBUG-94360）。"""

    def setUp(self):
        from qwen_app import chat_bridge
        self._module_path = chat_bridge.__file__
        with open(self._module_path, encoding="utf-8") as f:
            self._src = f.read()
        self._tree = ast.parse(self._src)

    def test_no_qml_signal_has_3plus_params(self):
        """扫描全部 pyqtSignal 定义，断言没有任何信号参数 >2。

        注意：pyqtProperty 的 notify= 信号也包括在扫描里（同一 pyqtSignal
        类，类级别定义，不是函数内 local）。
        """
        bad_signals = []
        # 扫描类级别（pyqtSignal 通常写在类体顶层）
        for node in ast.walk(self._tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if not isinstance(target, ast.Name):
                            continue
                        # 必须赋值给 pyqtSignal(...) 或 pyqtProperty(...)
                        if isinstance(stmt.value, ast.Call):
                            self._check_call(stmt.value, target.id, bad_signals)
                        elif isinstance(stmt.value, ast.Call) is False and isinstance(stmt.value, ast.Name):
                            # 可能是链式赋值，如 `a = b = pyqtSignal(...)`
                            pass

        if bad_signals:
            msg = "\n".join(
                f"  - {name}: {len_args} 参数（参数: {[a.arg for a in args]})"
                for name, len_args, args in bad_signals
            )
            self.fail(
                f"chat_bridge.py 含 {len(bad_signals)} 个 ≥3 参数信号，"
                f"QML Connections 监听会触发 QTBUG-94360 栈越界崩溃：\n{msg}"
            )

    def _check_call(self, call, name, bad_signals):
        """递归检查 Call 节点，找出 pyqtSignal(...) 直接调用。"""
        func = call.func
        if isinstance(func, ast.Name) and func.id == "pyqtSignal":
            args = call.args
            if len(args) > 2:
                bad_signals.append((name, len(args), args))
            return
        # 嵌套：pyqtProperty(fget, fset, ..., notify=someSignal)
        if isinstance(func, ast.Name) and func.id == "pyqtProperty":
            for kw in call.keywords:
                if kw.arg == "notify":
                    if isinstance(kw.value, ast.Call):
                        # notify=pyqtSignal(...) 内联
                        self._check_call(kw.value, f"{name}.notify", bad_signals)


class TestWorkerInternalSignals(unittest.TestCase):
    """worker.py 的 tool_call_result 3 参数是 Python-to-Python 内部信号，
    不被 QML 监听，可以保留 —— 但显式记录以便未来审计。"""

    def test_tool_call_result_remains_3_params(self):
        """worker.py.tool_call_result 应保持 3 参数（Python 跨线程信号）。"""
        from qwen_app import worker
        with open(worker.__file__, encoding="utf-8") as f:
            src = f.read()
        # 显式标注 3 参数保留意图
        self.assertIn("tool_call_result = pyqtSignal(str, str, str)", src,
                      "worker.py 是 Python 跨线程信号，3 参数保留合法")
        # 注意：如果以后这里改成 ≤2 参数 + QVariantMap，
        # chat_bridge 那边需相应调整 connect（toolCallStarted 已是 2 参数）


class TestToolCallResultInterface(unittest.TestCase):
    """验证 chat_bridge.toolCallResult 的新 2 参数 + QVariantMap 合约。"""

    def test_tool_call_result_emits_2_args(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        captured = []
        b.toolCallResult.connect(lambda n, payload: captured.append((n, payload)))
        # 直接调 _on_worker_tool_call_result（模拟 worker emit）
        b._on_worker_tool_call_result("get_weather", '{"city":"上海"}', "晴天 25℃")
        self.assertEqual(len(captured), 1)
        name, payload = captured[0]
        self.assertEqual(name, "get_weather")
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("args"), '{"city":"上海"}')
        self.assertEqual(payload.get("result"), "晴天 25℃")


if __name__ == "__main__":
    unittest.main()
