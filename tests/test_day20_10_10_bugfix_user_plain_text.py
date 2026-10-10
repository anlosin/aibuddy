# -*- coding: utf-8 -*-
"""Day 20.10.10 二次修复：user 气泡 RichText→PlainText 让 XML/URL/HTML 源码按字符折行

根因（offscreen 探针实测）：
  RichText 模式下 Qt 把 '<' '>' 当 HTML 标签边界做宽度估算，把 XML/HTML 源码
  里大量 '<' '>' 字符的可视宽度算成 0，导致 implicitWidth 估算偏小，
  WrapAnywhere 误判为"能装下"而不折行。
    - RichText: bubble.implicitHeight=17, TextEdit.paintedHeight=17（只 1 行）
    - PlainText: bubble.implicitHeight=144, TextEdit.paintedHeight=120（~7 行）

修复：user 气泡 + tool_call/tool_result 改 PlainText（字面宽度，无需 markdown
渲染）；ai 保留 RichText（流式累积阶段后 response_complete 触发 messageReplaced
用 chat_render 转 HTML 替换，真正显示给用户的是渲染版 RichText）。

护栏策略：花括号配对取 bodyText 块 + 静态契约 + offscreen 真加载 + 实测 paintedHeight。
测试里 inline 一个最小复现 QML（避开 QML import 路径兼容问题）。
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BUBBLE_PATH = os.path.join(_ROOT, "qwen_app", "qml", "MessageBubble.qml")


def _strip_qml_comments(src):
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    out = re.sub(r"//[^\n]*", "", out)
    return out


def _find_block_for_id(src, control_id):
    m = re.search(rf"id\s*:\s*{re.escape(control_id)}\b", src)
    if not m:
        return None
    before = src[max(0, m.start() - 250): m.start()]
    head_pat = re.compile(r"(TextEdit|Text|Rectangle|Item|Column|Row|Menu|"
                          r"Dialog|MouseArea)\s*\{")
    h = list(head_pat.finditer(before))
    if not h:
        return None
    start = max(0, m.start() - 250) + h[-1].start()
    depth = 0
    i = start
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
        i += 1
    return None


class TestBodyTextFormatByRole(unittest.TestCase):
    """bodyText.textFormat 必须按 who 角色分：user/tool→PlainText, ai→RichText"""

    def setUp(self):
        with open(BUBBLE_PATH, encoding="utf-8") as f:
            self.src = f.read()
        self.code = _strip_qml_comments(self.src)

    def test_body_text_format_uses_three_branch(self):
        """bodyText.textFormat 必须含 user/tool/ai 三分支判断（不是纯 isTool 二元）"""
        block = _find_block_for_id(self.code, "bodyText")
        self.assertIsNotNone(block)
        self.assertIn("isUser", block,
                      "bodyText.textFormat 必须按 isUser 分支（user 走 PlainText）")
        self.assertIn("isTool", block, "bodyText 必须有 isTool 分支")
        self.assertIn("PlainText", block)
        self.assertIn("RichText", block)

    def test_user_branch_uses_PlainText(self):
        """user 气泡用 PlainText（不解析 XML/HTML 标签的宽度）

        实际代码形态：(bubble.isTool || bubble.isUser) ? TextEdit.PlainText : TextEdit.RichText
        测试用宽松匹配：textFormat 行段必须包含 isUser + PlainText + 整体三元结构。
        """
        block = _find_block_for_id(self.code, "bodyText")
        # 取 textFormat 整段（可能跨多行）
        m = re.search(r"textFormat:\s*(.*?)\n\s+readOnly",
                      block, flags=re.DOTALL)
        self.assertIsNotNone(m, "bodyText 缺 textFormat 设置")
        expr = m.group(1)
        # isUser 与 PlainText 在同一段（中间可有空格 / 换行 / 括号）
        self.assertIn("isUser", expr, "textFormat 表达式必须含 isUser")
        self.assertIn("PlainText", expr, "textFormat 表达式必须含 PlainText")
        self.assertIn("RichText", expr, "textFormat 表达式必须含 RichText（ai 分支）")
        # isUser 在 PlainText 之前
        self.assertLess(expr.find("isUser"), expr.find("PlainText"),
                        "isUser 必须在 PlainText 之前（user 走 PlainText）")
        # RichText 在 isUser/PlainText 之后
        self.assertLess(expr.find("PlainText"), expr.find("RichText"),
                        "RichText 必须在 PlainText 之后（ai 分支）")

    def test_ai_branch_keeps_RichText(self):
        """ai 气泡保留 RichText 渲染（markdown 走 chat_render 渲染后由 messageReplaced 替换）"""
        block = _find_block_for_id(self.code, "bodyText")
        m = re.search(r"textFormat:\s*(.*?)\n\s+readOnly",
                      block, flags=re.DOTALL)
        self.assertIsNotNone(m)
        expr = m.group(1)
        # 整体必须是三元表达式：cond ? X : Y
        self.assertIn("?", expr, "textFormat 必须是三元表达式")
        # ai 分支（false 分支）必须是 RichText
        self.assertRegex(expr, r":\s*TextEdit\.RichText",
                         "ai 分支必须 TextEdit.RichText")

    def test_wrap_mode_still_anywhere(self):
        """wrapMode: TextEdit.WrapAnywhere 仍保留（防无空格长串溢出）"""
        block = _find_block_for_id(self.code, "bodyText")
        self.assertRegex(block, r"wrapMode\s*:\s*TextEdit\.WrapAnywhere",
                         "wrapMode 必须保持 WrapAnywhere（字符边界折行）")

    def test_select_attrs_unchanged(self):
        """Day 20.6.22 四件套（可选中）必须保留"""
        block = _find_block_for_id(self.code, "bodyText")
        for kw, want in (("selectByMouse", "true"),
                         ("selectByKeyboard", "true"),
                         ("readOnly", "true"),
                         ("persistentSelection", "true")):
            m = re.search(rf"\b{kw}\s*:\s*(\w+)", block)
            self.assertIsNotNone(m, f"bodyText 缺 {kw}")
            self.assertEqual(m.group(1), want, f"bodyText.{kw}={want}")


# XML 长串：实测确认触发 RichText→PlainText 修复
_LONG_XML = (
    '<GlobalNamingResources>'
    '<Resource name="UserDatabase" auth="Container" '
    'type="org.apache.catalina.UserDatabase" '
    'description="User database that can be updated and saved">'
    '</Resource></GlobalNamingResources>'
)


# inline probe：复刻 user 气泡结构（inline + 显式 textFormat 参数），
# 模拟 user + tool_call + tool_result + ai 四种 who 角色。
def _build_inline_probe(who, textformat):
    return f'''
import QtQuick 2.15
import QtQuick.Window 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15

Window {{
    id: win
    width: 900
    height: 400
    visible: true
    Item {{
        width: 720
        height: 400
        anchors.centerIn: parent
        ColumnLayout {{
            id: msgRow
            anchors.fill: parent
            spacing: 6
            RowLayout {{
                Layout.fillWidth: true
                spacing: 0
                Item {{ Layout.fillWidth: true; Layout.preferredHeight: 1 }}
                Rectangle {{
                    objectName: 'probeBubble'
                    id: bubble
                    color: '#4F8AF7'
                    radius: 16
                    property int preferredWidth: 680
                    property int maximumWidth: 680
                    width: maximumWidth > 0 ? Math.min(preferredWidth, maximumWidth) : preferredWidth
                    implicitWidth: preferredWidth
                    implicitHeight: body.implicitHeight + 24
                    TextEdit {{
                        id: body
                        anchors.fill: parent
                        anchors.margins: 12
                        text: '{_LONG_XML}'
                        color: '#FFFFFF'
                        wrapMode: TextEdit.WrapAnywhere
                        readOnly: true
                        textFormat: {textformat}
                    }}
                }}
                Item {{ Layout.preferredHeight: 1 }}
            }}
        }}
    }}
}}
'''


def _load_probe(qml_text):
    """inline 加载 QML，返回 root；失败抛 AssertionError + warnings。"""
    from PyQt5.QtCore import QUrl, QTimer, QEventLoop
    from PyQt5.QtQml import QQmlApplicationEngine
    probe_path = os.path.join(_ROOT, "tests", "_probe_user_wrap.qml")
    with open(probe_path, "w", encoding="utf-8") as f:
        f.write(qml_text)
    try:
        engine = QQmlApplicationEngine()
        engine.addImportPath(_ROOT)
        engine.load(QUrl.fromLocalFile(probe_path))
        roots = engine.rootObjects()
        if not roots:
            raise AssertionError(
                "QML root 未加载。\n"
                "  探针：inline Rectangle + TextEdit + 长 XML，textFormat "
                "应该是 PlainText（user 气泡修复后）。\n"
                "  1) textFormat 字符串拼写是否正确（PlainText / RichText）\n"
                "  2) XML 字符串里若有单引号未转义也会导致 QML 解析失败")
        root = roots[0]
        # 等一帧让 paintedHeight 计算稳定
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec_()
        return root, engine
    finally:
        try:
            os.remove(probe_path)
        except FileNotFoundError:
            pass


class TestUserBubblePlainTextRuntime(unittest.TestCase):
    """user 气泡 + 长 XML + PlainText 模式实测 paintedHeight（防回归）"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtGui import QGuiApplication
        import sys as _sys
        cls.app = QGuiApplication.instance() or QGuiApplication(_sys.argv)

    def setUp(self):
        # 模拟 user 气泡：PlainText 模式（修复后行为）
        self.root, self.engine = _load_probe(
            _build_inline_probe("user", "TextEdit.PlainText"))

    def test_painted_height_multiline(self):
        b = self.root.findChild(object, "probeBubble")
        self.assertIsNotNone(b, "probeBubble 未找到")
        # 找 body TextEdit
        body = None
        for c in b.findChildren(object):
            if c.metaObject().className().startswith("QQuickTextEdit"):
                body = c
                break
        self.assertIsNotNone(body, "body TextEdit 未找到")
        ph = body.property("paintedHeight")
        # 修复后 PlainText 模式 ~7 行 ≈ 120px
        # 设保守下限 30（≥2 行 × 15px），防回归到 RichText 模式（17px）
        self.assertGreater(ph, 30,
                           f"PlainText 模式 paintedHeight={ph}，XML 没折成多行")


class TestUserBubbleRichTextRegression(unittest.TestCase):
    """对照实验：RichText 模式（修复前）XML 仍只 1 行（验证修复必要性）"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtGui import QGuiApplication
        import sys as _sys
        cls.app = QGuiApplication.instance() or QGuiApplication(_sys.argv)

    def setUp(self):
        self.root, self.engine = _load_probe(
            _build_inline_probe("user", "TextEdit.RichText"))

    def test_rich_text_squeezes_xml(self):
        """RichText 模式下 XML 被 '<' '>' 边界挤成 1 行（证明修复必要性）"""
        b = self.root.findChild(object, "probeBubble")
        body = None
        for c in b.findChildren(object):
            if c.metaObject().className().startswith("QQuickTextEdit"):
                body = c
                break
        self.assertIsNotNone(body)
        ph = body.property("paintedHeight")
        # RichText 模式 paintedHeight ≈ 17（1 行）
        # 这就是用户截图里的 bug 现象
        self.assertLess(ph, 40,
                        f"RichText 模式意外折行（paintedHeight={ph}），"
                        f"无法证明 RichText 模式是 bug 根因")


if __name__ == "__main__":
    unittest.main()
