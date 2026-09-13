"""Day 20: QML 气泡相关冒烟 / 静态护栏（T04）

继承 test_qml_smoke 的 3 道护栏精神：
1. Main.qml + MessageBubble.qml 真加载 + 渲染
2. 静态检查 Day 20 新增的 QML 信号 handler 对应的 Python 信号参数 ≤2
3. 真实启动器跑一遍 + 顶层窗口存活（继承 test_qml_smoke 的 TestLauncherKeepsWindowAlive）
4. 新增 Day 20 专项：MessageBubble.qml 含 Menu / Dialog 组件 + 8 个 bridge.* 调用

为何不直接继承 TestQmlStartupSmoke：
- LOAD 次数保持 3（防随机崩溃漏检）
- 但 Day 20 新增的 menuBar / 三点菜单 / Popup / Dialog / 3 个 Connections handler
  任何一个出问题都可能引发新的 0xC0000005，所以单独写一遍
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_QML = os.path.join(ROOT, "qwen_app", "qml", "Main.qml")
BUBBLE_QML = os.path.join(ROOT, "qwen_app", "qml", "MessageBubble.qml")
LOADS = 3

# 真渲染 + 退出码守护（防 QML id forward ref / 循环 binding 引发 0xC0000005）
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
# 800ms 让 bubble / menu / dialog 都初始化完
QTimer.singleShot(800, app.quit)
app.exec_()
print("RENDER_OK", flush=True)
"""


class TestDay20MainQmlStartup(unittest.TestCase):
    """Day 20 Main.qml 必须能加载 + 渲染（含 menuBar / 三点菜单 / Popup）"""

    def test_main_qml_with_menubar_and_three_dot_menu(self):
        code = _LOADER.format(root=ROOT, qml=MAIN_QML)
        env = dict(os.environ)
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
            "Day 20 Main.qml 加载时进程异常退出（%d 次里失败 %d 次）。"
            "新增的 menuBar / 三点菜单 / Dialog 任何一处出 id forward ref"
            "或 binding 循环都可能引发 0xC0000005。\n%s" % (LOADS, len(failures), failures))


class TestDay20MessageBubbleComponents(unittest.TestCase):
    """MessageBubble.qml 应包含三点菜单 + 编辑 Dialog 的所有必备组件（静态）"""

    @classmethod
    def setUpClass(cls):
        with open(BUBBLE_QML, encoding="utf-8") as f:
            cls.src = f.read()

    def test_has_msg_index_property(self):
        """必须有 property int msgIndex: -1（三点菜单依赖它定位 history）"""
        self.assertRegex(
            self.src, r"property\s+int\s+msgIndex",
            "MessageBubble.qml 必须声明 msgIndex 属性")

    def test_has_more_button(self):
        """必须含 moreBtn / 三点按钮"""
        self.assertIn("moreBtn", self.src, "缺少三点按钮 id 'moreBtn'")
        self.assertIn("\u22ef", self.src, "三点字符 ⋯ 缺失")

    def test_has_bubble_menu(self):
        """必须含 Menu id 'bubbleMenu'"""
        self.assertRegex(self.src, r"Menu\s*\{[^}]*id:\s*bubbleMenu",
                         "缺少 Menu id 'bubbleMenu'")

    def test_has_edit_dialog(self):
        """必须含 Dialog id 'editDialog'（编辑用户消息）"""
        self.assertRegex(self.src, r"Dialog\s*\{[^}]*id:\s*editDialog",
                         "缺少 Dialog id 'editDialog'")

    def test_uses_all_eight_bridge_slots(self):
        """8 个 bridge.* slot 全部应被 MessageBubble.qml 引用"""
        # 注意：MenuBar 路径 Main.qml 用了一些，但 MessageBubble 也独立用了一些
        expected = [
            "copy_to_clipboard",
            "copy_code",
            "delete_bubble",
            "edit_user_message",
            "regenerate_ai_response",
            "quote_reply",
            "speak_text",
            "share_bubble",
        ]
        missing = [s for s in expected if s not in self.src]
        self.assertEqual(
            missing, [],
            "MessageBubble.qml 缺失以下 bridge.* slot 调用：%s" % missing)

    def test_conditional_menu_items_have_visibility_guards(self):
        """条件菜单项必须有 visible / enabled 守卫（按 who 类型）"""
        # 编辑项 visible: bubble.isUser
        self.assertRegex(self.src, r"visible:\s*bubble\.isUser",
                         "「编辑」菜单项缺 isUser 守卫")
        # 重新生成 visible: bubble.who === \"ai\"
        self.assertRegex(self.src, r"visible:\s*bubble\.who\s*===\s*\"ai\"",
                         "「重新生成」菜单项缺 ai 守卫")
        # 引用 / 朗读 visible: !bubble.isTool
        self.assertIn("!bubble.isTool", self.src,
                      "「引用/朗读」菜单项缺 !isTool 守卫")
        # 复制代码 visible: bubble.hasCode
        self.assertRegex(self.src, r"visible:\s*bubble\.hasCode",
                         "「复制代码」菜单项缺 hasCode 守卫")


class TestMainQmlMenuBarComponents(unittest.TestCase):
    """Main.qml 应包含 menuBar + 5 组 Menu + toast / Popup"""

    @classmethod
    def setUpClass(cls):
        with open(MAIN_QML, encoding="utf-8") as f:
            cls.src = f.read()

    def test_has_menu_bar(self):
        self.assertRegex(self.src, r"menuBar:\s*MenuBar\s*\{",
                         "Main.qml 缺 menuBar: MenuBar { ... }")

    def test_has_five_menus(self):
        """5 组菜单：文件 / 编辑 / 视图 / 聊天 / 帮助"""
        for title in ["&文件", "&编辑", "&视图", "&聊天", "&帮助"]:
            self.assertIn(title, self.src, "Menu 缺 title '%s'" % title)

    def test_has_toast_box(self):
        """toastBox + onToast handler 必须存在"""
        self.assertIn("toastBox", self.src, "缺 toastBox Rectangle")
        self.assertRegex(self.src, r"function\s+onToast\s*\(",
                         "缺 onToast(msg) handler")

    def test_has_bubble_deleted_handler(self):
        """onBubbleDeleted 必须存在（delete_bubble → QML 同步移除）"""
        self.assertRegex(self.src, r"function\s+onBubbleDeleted\s*\(",
                         "缺 onBubbleDeleted(idx) handler")

    def test_has_quote_inserted_handler(self):
        """onQuoteInserted 必须存在（引用回复填输入框）"""
        self.assertRegex(self.src, r"function\s+onQuoteInserted\s*\(",
                         "缺 onQuoteInserted(quoted) handler")

    def test_message_bubble_inject_msgindex(self):
        """delegate 注入 msgIndex: index"""
        # 找 MessageBubble { ... msgIndex: index ... }
        self.assertRegex(self.src, r"MessageBubble\s*\{[^}]*msgIndex:\s*index",
                         "Main.qml delegate 没把 ListView.index 注入 msgIndex")

    def test_has_help_and_about_popups(self):
        """Popup: helpPopup / aboutPopup"""
        self.assertIn("helpPopup", self.src, "缺 helpPopup")
        self.assertIn("aboutPopup", self.src, "缺 aboutPopup")


class TestDay20SignalArity(unittest.TestCase):
    """Day 20 新增的 4 个 emit 信号都 ≤2 参数（QTBUG-94360 硬约束）"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, ROOT)
        from PyQt5.QtCore import QMetaMethod
        from qwen_app.chat_bridge import ChatBridge
        arities = {}
        mo = ChatBridge.staticMetaObject
        for i in range(mo.methodCount()):
            m = mo.method(i)
            if m.methodType() != QMetaMethod.Signal:
                continue
            sig = bytes(m.methodSignature()).decode("ascii", "replace")
            name, _, rest = sig.partition("(")
            params = rest.rstrip(")")
            arities[name] = 0 if not params.strip() else len(params.split(","))
        cls.arities = arities

    def test_bubble_deleted_one_param(self):
        """bubbleDeleted 信号 ≤2 参数"""
        self.assertIn("bubbleDeleted", self.arities)
        self.assertLessEqual(self.arities["bubbleDeleted"], 2,
            "bubbleDeleted 参数 >2 触发 QTBUG-94360")

    def test_bubble_edited_two_params(self):
        """bubbleEdited 信号 ≤2 参数"""
        self.assertIn("bubbleEdited", self.arities)
        self.assertLessEqual(self.arities["bubbleEdited"], 2)

    def test_quote_inserted_one_param(self):
        self.assertLessEqual(self.arities["quoteInserted"], 2)

    def test_toast_one_param(self):
        self.assertLessEqual(self.arities["toast"], 2)

    def test_day20_handlers_all_have_valid_signals(self):
        """Main.qml 里 onBubbleDeleted / onQuoteInserted / onToast 对应的 Python 信号都存在"""
        with open(MAIN_QML, encoding="utf-8") as f:
            qml = f.read()
        handlers = set()
        for raw in re.findall(r"function\s+on([A-Z]\w*)\s*\(", qml):
            handlers.add(raw[0].lower() + raw[1:])
        # Day 20 新增
        for sig in ("bubbleDeleted", "quoteInserted", "toast"):
            self.assertIn(sig, handlers,
                         "Main.qml 没监听到 Day 20 新信号 %s" % sig)
            self.assertIn(sig, self.arities,
                         "ChatBridge 上找不到 Day 20 新信号 %s" % sig)


class TestLauncherWindowAliveWithDay20(unittest.TestCase):
    """Day 20 新增 menuBar / 三点菜单后，run_qtquick 仍必须留下顶层窗口

    继承 test_qml_smoke.TestLauncherKeepsWindowAlive 的逻辑：
    QQmlApplicationEngine 只被局部变量引用会被 GC，新组件越多越容易踩到这个坑。
    """

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
mb = wins[0].property("menuBar") if wins else None
print("VERDICT=%s ok=%s windows=%d menuBar=%s titles=%r"
      % ("PASS" if (ok and wins and mb is not None) else "FAIL",
         ok, len(wins), mb is not None,
         [w.title() for w in wins]), flush=True)
"""

    def test_run_qtquick_leaves_toplevel_with_menubar(self):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_LOGGING_RULES"] = "qt.qpa.*=false"

        p = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", self._WINDOW_CHECKER.format(root=ROOT)],
            capture_output=True, text=True, env=env, cwd=ROOT)

        verdict = [l for l in (p.stdout or "").splitlines() if l.startswith("VERDICT=")]
        self.assertTrue(
            verdict,
            "窗口检查器没有输出判定行（可能启动就挂了）。\nstdout:\n%s\nstderr:\n%s"
            % ((p.stdout or "")[-800:], (p.stderr or "")[-800:]))
        self.assertIn(
            "VERDICT=PASS", verdict[0],
            "Day 20 Main.qml 启动后没有 menuBar / 没有顶层窗口。\n"
            "最常见原因：menuBar 字段引用 forward decl / QQmlApplicationEngine 被 GC 回收。\n"
            "实测输出：%s" % verdict[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)