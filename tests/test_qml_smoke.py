"""QtQuick 启动冒烟 / 信号参数约束测试（Day 17 新增）。

背景
----
启动期 `0xC0000005` 曾经的真凶是 QTBUG-94360：
QML 的 `Connections { target: <pythonObject> }` 去连接**参数 ≥3 个**的 Python 信号，
会在 `QQmlConnections::connectSignalsToMethods` → `QQmlBoundSignalExpression` →
`QV4::Function::updateInternalClass` 上栈越界。Qt 5.15.2 受影响，修复版本是 Qt 6.0，
而 PyQt5 绑定在 5.15.x，升级不了 —— 只能靠「QML 监听的信号参数 ≤2」来规避。

这个崩溃发生在 Qt/C++ 层，带随机性，且概率随 QML 体量升高：
- Python 的 try/except 抓不到；
- 普通单元测试（只测 Python 对象）覆盖不到；
- 极易被误判成渲染 / GPU 问题（实测 offscreen 也复现）。

所以这里放三道护栏：
1. 真的把 Main.qml 加载 + 渲染一遍，看进程退出码；
2. 静态检查：Main.qml 里实际出现的每个 `function onXxx(...)`，
   其对应的 ChatBridge 信号参数必须 ≤2。
3. 用真实启动器 `main.run_qtquick()` 拉起一次，断言确实产生了顶层窗口。

第 3 条防的是另一类静默故障：`QQmlApplicationEngine` 若只作为函数局部变量，
函数返回后会被 GC 回收（PyQt 持有其所有权），其创建的 QML 根窗口一并销毁 ——
进程活着、`app.exec_()` 在跑、启动日志照常打印，但**一个窗口都没有**。
这类问题同样抓不到：不崩、不报错、Python 层无异常。
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_QML = os.path.join(ROOT, "qwen_app", "qml", "Main.qml")
LOADS = 3

_LOADER = r"""
import sys
sys.path.insert(0, r"{root}")
from PyQt5.QtCore import QUrl, QTimer
from PyQt5.QtQml import QQmlApplicationEngine
from PyQt5.QtWidgets import QApplication
from qwen_app.chat_bridge import ChatBridge

app = QApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.warnings.connect(
    lambda ws: [print("QMLWARN:", w.toString(), flush=True) for w in ws])
bridge = ChatBridge(theme="light")
engine.rootContext().setContextProperty("bridge", bridge)
engine.load(QUrl.fromLocalFile(r"{qml}"))
if not engine.rootObjects():
    print("LOAD_FAILED_NO_ROOT", flush=True)
    raise SystemExit(2)
QTimer.singleShot(800, app.quit)
app.exec_()
print("RENDER_OK", flush=True)
"""


class TestQmlStartupSmoke(unittest.TestCase):
    """Main.qml 必须能被真的加载 + 渲染，且进程不异常退出。"""

    def test_main_qml_loads_without_native_crash(self):
        code = _LOADER.format(root=ROOT, qml=MAIN_QML)
        env = dict(os.environ)
        # 默认 offscreen：不弹窗。实测 offscreen 同样能复现该原生崩溃，护栏有效。
        env.setdefault("QT_QPA_PLATFORM", "offscreen")

        failures = []
        for i in range(LOADS):
            p = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=env)
            if p.returncode != 0:
                failures.append({
                    "run": i + 1,
                    "returncode": p.returncode,
                    "tail": (p.stdout or "")[-300:] + (p.stderr or "")[-800:],
                })

        self.assertEqual(
            failures, [],
            "Main.qml 加载时进程异常退出（%d 次里失败 %d 次）。"
            "最常见原因：QML Connections 监听了参数 ≥3 个的 Python 信号"
            "（QTBUG-94360，Qt 5.15.2）。\n%s" % (LOADS, len(failures), failures))


class TestQmlSignalArity(unittest.TestCase):
    """静态护栏：QML 能连到的信号，参数不能超过 2 个（QTBUG-94360）。"""

    @staticmethod
    def _signal_arities():
        """从 QMetaObject 读出 ChatBridge 全部信号的参数个数。"""
        sys.path.insert(0, ROOT)
        from PyQt5.QtCore import QMetaMethod
        from qwen_app.chat_bridge import ChatBridge

        mo = ChatBridge.staticMetaObject
        out = {}
        for i in range(mo.methodCount()):
            m = mo.method(i)
            if m.methodType() != QMetaMethod.Signal:
                continue
            sig = bytes(m.methodSignature()).decode("ascii", "replace")
            name, _, rest = sig.partition("(")
            params = rest.rstrip(")")
            out[name] = 0 if not params.strip() else len(params.split(","))
        return out

    @staticmethod
    def _qml_connected_handlers():
        """Main.qml 里出现的 `function onXxx(` → 信号名 xxx。"""
        with open(MAIN_QML, encoding="utf-8") as f:
            qml = f.read()
        names = set()
        for raw in re.findall(r"function\s+on([A-Z]\w*)\s*\(", qml):
            names.add(raw[0].lower() + raw[1:])
        return names

    def test_qml_connected_signals_have_at_most_two_args(self):
        arities = self._signal_arities()
        self.assertIn("messageAdded", arities, "没能从 QMetaObject 读到信号，测试失效")

        offenders = []
        for name in sorted(self._qml_connected_handlers()):
            n = arities.get(name)
            if n is None:
                # QML 监听的信号在 ChatBridge 上不存在 → 该 handler 永远不会触发
                offenders.append("%s: ChatBridge 上没有这个信号" % name)
            elif n > 2:
                offenders.append("%s: %d 个参数" % (name, n))

        self.assertEqual(
            offenders, [],
            "QML Connections 监听的信号必须 ≤2 个参数，否则 Qt 5.15.2 会在启动期"
            "栈越界崩溃（QTBUG-94360）。请把多余字段打包成 QVariantMap / "
            "QVariantList 放进第 2 个参数。违规项：\n  - %s" % "\n  - ".join(offenders))


_WINDOW_CHECKER = r"""
import os, sys
sys.path.insert(0, r"{root}")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
import main as launcher

app = QApplication(sys.argv)
launcher._set_qt_attributes()
launcher._setup_qt_app(app)
ok = launcher.run_qtquick(app)
wins = app.topLevelWindows()
print("VERDICT=%s ok=%s windows=%d titles=%r"
      % ("PASS" if (ok and wins) else "FAIL", ok, len(wins),
         [w.title() for w in wins]), flush=True)
"""


class TestLauncherKeepsWindowAlive(unittest.TestCase):
    """真实启动器跑一遍：必须留下一个顶层窗口。

    防「QQmlApplicationEngine 被 GC 回收 → 静默无界面」：
    engine 作为局部变量时函数返回即析构，其 QML 根窗口一起消失。
    """

    def test_run_qtquick_leaves_a_toplevel_window(self):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"      # 不弹窗，但仍能判定窗口是否存活
        env["QT_LOGGING_RULES"] = "qt.qpa.*=false"

        p = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", _WINDOW_CHECKER.format(root=ROOT)],
            capture_output=True, text=True, env=env, cwd=ROOT)

        verdict = [l for l in (p.stdout or "").splitlines() if l.startswith("VERDICT=")]
        self.assertTrue(
            verdict,
            "窗口检查器没有输出判定行（可能启动就挂了）。\nstdout:\n%s\nstderr:\n%s"
            % ((p.stdout or "")[-800:], (p.stderr or "")[-800:]))
        self.assertIn(
            "VERDICT=PASS", verdict[0],
            "run_qtquick() 之后没有任何顶层窗口 —— 界面不会显示。\n"
            "最常见原因：QQmlApplicationEngine 只被局部变量引用，函数返回后被 GC 回收，"
            "它创建的 QML 根窗口随之销毁。请在模块级（如 main._KEEP_ALIVE）持有引用。\n"
            "实测输出：%s" % verdict[0])


if __name__ == "__main__":
    unittest.main()
