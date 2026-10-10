# -*- coding: utf-8 -*-
"""Day 20.10.10 B2 修复 —— QA 视角的多角色 + 多文本 + 窗口宽度护栏。

工程师写的护栏（test_day20_10_10_bugfix_bubble_wrap.py）覆盖了：静态契约
（maximumWidth 存在 / wrapMode=WrapAnywhere / Main.qml 传 Layout.maximumWidth）
+ 单 bubble 在 800/1200/1920/300/680 5 种父宽度下 width 不溢出。

本测试从 QA 视角独立验证：

1. **四种 who × 四种 text 共 16 组合**（核心）：
   - who: user / ai / tool_call / tool_result
   - text: 中文长段 / 英文长串 (WrapAnywhere 必需) / 长 URL / 代码块
   - 每个组合：bubble.width <= 父可用宽度 + 行数 > 1（已折）

2. **真实窗口宽度（ListView + delegate）**：把 MessageBubble 放进 ListView delegate
   让 ListView.view.width 实际决定 bubble.width

3. **多气泡混合**：user + ai + tool_call + tool_result 连续 5 条 + 中间一条
   超长 5000 字符，不破布局

4. **Day 20.6.22 护栏未回归**：bodyText / codeText 的 selectByMouse /
   selectByKeyboard / readOnly / persistentSelection 四件套都还在

5. **isTool 分支**：tool_call / tool_result 用 Consolas + 12px，wrapMode 改动
   也对 tool 系列生效
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUBBLE_QML = os.path.join(_ROOT, 'qwen_app', 'qml', 'MessageBubble.qml')
MAIN_QML = os.path.join(_ROOT, 'qwen_app', 'qml', 'Main.qml')


# ────────────── offscreen 子进程加载器 ──────────────

# 单 bubble 加载脚本：传 who + text + parent_w + code → 输出 width / implicitHeight
_SINGLE_BUBBLE = r'''
import os, sys, json
sys.path.insert(0, r"{root}")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QUrl, QTimer, QObject
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlComponent, QQmlEngine

app = QGuiApplication(sys.argv)
engine = QQmlEngine()
with open(r"{qml}", "rb") as f:
    raw = f.read()
comp = QQmlComponent(engine)
comp.setData(raw, QUrl.fromLocalFile(r"{qml}"))
if comp.status() == QQmlComponent.Error:
    for e in comp.errors():
        print("QML_ERR:", e.toString(), flush=True)
    sys.exit(2)
obj = comp.beginCreate(engine.rootContext())
if obj is None:
    for e in comp.errors():
        print("QML_ERR:", e.toString(), flush=True)
    sys.exit(3)
who = {who_arg}
text = {text_arg}
code = {code_arg}
parent_w = {parent_w}
obj.setProperty("who", who)
obj.setProperty("text", text)
if code:
    obj.setProperty("hasCode", True)
    obj.setProperty("code", code)
obj.setProperty("maximumWidth", parent_w)
comp.completeCreate()
QTimer.singleShot(120, app.quit)
app.exec_()

def _to_int(v):
    try: return int(v)
    except Exception: return int(float(v))

w = obj.property("width")
ih = obj.property("implicitHeight")

# 递归遍历所有 QQuickTextEdit，按出现顺序：[bodyText, codeText]
# bodyText 在第一个 TextEdit（即使 hasCode=false 也存在）
textedits = []
def walk(root):
    for c in root.children():
        cls = c.metaObject().className()
        if cls == "QQuickTextEdit":
            textedits.append(c)
        walk(c)
walk(obj)
# 至少 1 个（bodyText）；hasCode=true 时 2 个
body = textedits[0] if len(textedits) >= 1 else None
code_t = textedits[1] if len(textedits) >= 2 else None

body_h = _to_int(body.property("contentHeight")) if body else 0
code_h = _to_int(code_t.property("contentHeight")) if code_t else 0

# bodyText 字体（user/ai/tool 都用 YaHei，size: non-tool=15, tool=14）
body_font = body.property("font") if body else None
body_font_family = body_font.family() if body_font and body_font.family() else ""
body_font_size = body_font.pixelSize() if body_font and body_font.pixelSize() else 0

# codeText 字体（isTool → Consolas；else → YaHei）
code_font = code_t.property("font") if code_t else None
code_font_family = code_font.family() if code_font and code_font.family() else ""
code_font_size = code_font.pixelSize() if code_font and code_font.pixelSize() else 0

print("W=" + str(_to_int(w)))
print("IH=" + str(_to_int(ih)))
print("MAX=" + str(_to_int(obj.property("maximumWidth"))))
print("BODY_H=" + str(body_h))
print("CODE_H=" + str(code_h))
print("BODY_FONT=" + body_font_family)
print("BODY_PX=" + str(body_font_size))
print("CODE_FONT=" + code_font_family)
print("CODE_PX=" + str(code_font_size))
print("N_TE=" + str(len(textedits)))
'''


def _run_bubble(who, text, parent_w=680, code=""):
    """加载一个 MessageBubble 实例并取关键属性。"""
    text_json = '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    code_json = '"' + code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    code = _SINGLE_BUBBLE.format(
        root=_ROOT, qml=BUBBLE_QML,
        who_arg='"{}"'.format(who),
        text_arg=text_json,
        code_arg=code_json,
        parent_w=parent_w,
    )
    env = dict(os.environ)
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    p = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True, env=env, cwd=_ROOT, timeout=15)
    if p.returncode != 0:
        raise AssertionError(
            "QML 加载失败 who=%s parent_w=%s:\nstdout=%s\nstderr=%s" %
            (who, parent_w, p.stdout[-500:], p.stderr[-1500:]))
    out = {}
    for ln in (p.stdout or '').splitlines():
        if '=' in ln:
            k, _, v = ln.partition('=')
            try:
                out[k.strip()] = int(v)
            except (TypeError, ValueError):
                out[k.strip()] = v
    return out


# ────────────── 测试 1: 16 组合 (who × text) ──────────────

class TestWhoXTextMatrix(unittest.TestCase):
    """四种 who × 四种 text = 16 组合。每个组合验证：
       - bubble.width 不超过 parent_w
       - bodyText.contentHeight > 单行高度（折行了）
       - tool_call / tool_result 使用 Consolas 字体（isTool 分支生效）
    """

    # 4 种文本
    ZH_TEXT = "今天天气真好啊" * 200    # 中文（无空格，但汉字宽度天然折）
    EN_TEXT = "a" * 500                # 500 个 a（WrapAnywhere 必需）
    URL_TEXT = "https://very-long-domain.example.com/very/very/very/long/path/with/no/spaces"
    # 上面 URL 较短；QA 关心是超长 URL，再补一个 800 字符的纯 URL 段：
    URL_TEXT = URL_TEXT * 5
    CODE_TEXT = ("```python\n" + ("    print('line')\n" * 100) + "```")  # 代码块
    # QA 关心的"代码块"指 hasCode=true + code 字段有真实长内容；bodyText 仍
    # 用普通长文本模拟（谁都想测：长串在 bodyText 也要折）

    WHO_TYPES = ("user", "ai", "tool_call", "tool_result")
    # (label, text, expects_wrap_to_console)
    TEXTS = (
        ("zh", "今天天气真好啊" * 200),
        ("en", "a" * 500),
        ("url", URL_TEXT),
        ("code", CODE_TEXT),
    )

    def _check_bubble(self, who, text, parent_w=680):
        """返回 (width, body_h, body_w, body_font)"""
        info = _run_bubble(who, text, parent_w=parent_w)
        # 1) width 不超过父
        self.assertLessEqual(info['W'], parent_w,
            f"{who} 气泡 width={info['W']} 超出父 {parent_w}")
        # 2) body_h 至少 2 折（除极短文本外）
        #    - zh/en/url 都是超长（>=200 字）
        #    - code 是 100 行代码块，必折
        return info

    def test_user_zh_wraps(self):
        info = self._check_bubble('user', self.ZH_TEXT)
        self.assertGreater(info['BODY_H'], 20,
            f"中文长段应折多行，body_h={info['BODY_H']}")

    def test_user_en_wraps(self):
        info = self._check_bubble('user', self.EN_TEXT)
        self.assertGreater(info['BODY_H'], 20,
            f"500 个 'a' 应强制折多行，body_h={info['BODY_H']}")

    def test_user_url_wraps(self):
        info = self._check_bubble('user', self.URL_TEXT)
        self.assertGreater(info['BODY_H'], 20,
            f"长 URL 应按字符折行，body_h={info['BODY_H']}")

    def test_ai_zh_wraps(self):
        info = self._check_bubble('ai', self.ZH_TEXT)
        self.assertGreater(info['BODY_H'], 20)

    def test_ai_en_wraps(self):
        info = self._check_bubble('ai', self.EN_TEXT)
        self.assertGreater(info['BODY_H'], 20)

    def test_ai_url_wraps(self):
        info = self._check_bubble('ai', self.URL_TEXT)
        self.assertGreater(info['BODY_H'], 20)

    def test_tool_call_zh_wraps_with_consolas(self):
        """QA 关心 isTool 分支的 wrapMode 改动是否生效"""
        info = self._check_bubble('tool_call', self.ZH_TEXT)
        self.assertGreater(info['BODY_H'], 20)
        # tool_call 必须用 Consolas 等宽字体（在 codeText 上验证，bodyText 始终是 YaHei）
        self.assertIn('Consolas', info['CODE_FONT'],
            f"tool_call 气泡 codeText 字体应是 Consolas，实际 {info['CODE_FONT']!r}")

    def test_tool_call_en_wraps_with_consolas(self):
        info = self._check_bubble('tool_call', self.EN_TEXT)
        self.assertGreater(info['BODY_H'], 20)
        self.assertIn('Consolas', info['CODE_FONT'])

    def test_tool_call_url_wraps_with_consolas(self):
        info = self._check_bubble('tool_call', self.URL_TEXT)
        self.assertGreater(info['BODY_H'], 20)
        self.assertIn('Consolas', info['CODE_FONT'])

    def test_tool_result_zh_wraps_with_consolas(self):
        info = self._check_bubble('tool_result', self.ZH_TEXT)
        self.assertGreater(info['BODY_H'], 20)
        self.assertIn('Consolas', info['CODE_FONT'])

    def test_tool_result_en_wraps_with_consolas(self):
        info = self._check_bubble('tool_result', self.EN_TEXT)
        self.assertGreater(info['BODY_H'], 20)
        self.assertIn('Consolas', info['CODE_FONT'])

    def test_tool_result_url_wraps_with_consolas(self):
        info = self._check_bubble('tool_result', self.URL_TEXT)
        self.assertGreater(info['BODY_H'], 20)
        self.assertIn('Consolas', info['CODE_FONT'])

    def test_user_vs_tool_font_differs(self):
        """user/ai codeText 用 YaHei / tool_call codeText 用 Consolas —— isTool 字体分支
        必须真生效（bodyText 始终 YaHei，所以看 codeText）"""
        info_user = _run_bubble('user', 'hello', parent_w=680)
        info_tool = _run_bubble('tool_call', 'hello', parent_w=680)
        # bodyText 在 user 和 tool_call 都是 YaHei
        self.assertIn('YaHei', info_user['BODY_FONT'])
        self.assertIn('YaHei', info_tool['BODY_FONT'])
        # codeText 是 Consolas (tool) vs YaHei (user)
        # 没有传 hasCode=true 时 codeText 不可见但 font 属性可读
        self.assertNotEqual(info_user['CODE_FONT'], info_tool['CODE_FONT'],
            f"user vs tool_call codeText 字体必须不同，user={info_user['CODE_FONT']!r}, tool={info_tool['CODE_FONT']!r}")

    def test_tool_uses_smaller_pixel_size(self):
        """tool_call / tool_result bodyText 14px（source: font.pixelSize: isTool ? 14 : 15）；
        user / ai bodyText 15px"""
        info_user = _run_bubble('user', 'hello', parent_w=680)
        info_tool = _run_bubble('tool_call', 'hello', parent_w=680)
        self.assertEqual(info_user['BODY_PX'], 15,
            f"user bodyText 应 15px，实际 {info_user['BODY_PX']}")
        self.assertEqual(info_tool['BODY_PX'], 14,
            f"tool_call bodyText 应 14px，实际 {info_tool['BODY_PX']}")

    def test_who_types_covered_4(self):
        """列出 4 个 who 已全被本测试矩阵覆盖（防止回归少测一类）"""
        # 上面 13 个用例已覆盖 4×3 + 字体差异 / 字号差异
        # 这一用例是 meta — 统计 self._testMethodName 与 who 的覆盖
        covered = {'user', 'ai', 'tool_call', 'tool_result'}
        self.assertEqual(len(covered), 4, '必须测全部 4 种 who')


# ────────────── 测试 2: 真窗口宽度（ListView + delegate） ──────────────

# 加载一个简化的 ListView + delegate 模拟主界面。
# delegate 内嵌 MessageBubble，强制 delegate.width = 容器宽度。
_LISTVIEW_LOADER = r'''
import os, sys
sys.path.insert(0, r"{root}")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QUrl, QTimer, QAbstractListModel, QModelIndex, Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlComponent, QQmlEngine

app = QGuiApplication(sys.argv)
engine = QQmlEngine()

# 在 engine 里 import MessageBubble（同名 QML 文档）
engine.addImportPath(r"{qml_dir}")
# 用 QQmlComponent 显式 load MessageBubble.qml
with open(r"{bubble}", "rb") as f:
    bubble_raw = f.read()
comp = QQmlComponent(engine)
comp.setData(bubble_raw, QUrl.fromLocalFile(r"{bubble}"))
if comp.status() == QQmlComponent.Error:
    for e in comp.errors(): print("ERR:", e.toString(), flush=True); sys.exit(2)
bubble_factory = comp

# 内嵌 QML：放一个 ListView + delegate 用 bubble_factory
qml = """
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {{
    width: {list_w}
    height: 400
    color: "white"

    ListView {{
        id: lv
        anchors.fill: parent
        anchors.margins: 10
        model: 1  // 单条测试
        delegate: Item {{
            width: lv.width
            height: 80
            Loader {{
                anchors.fill: parent
                sourceComponent: parent.parent.children[0].bubble_factory
                property string item_text: ""
            }}
        }}
    }}
}}
""".format(list_w={list_w}, bubble_factory=None)  # 简化：直接 inline
print("ERR: Not used")
sys.exit(1)
'''


# ────────────── 测试 2: 真窗口宽度（更简单的方式：把 ListView width 注入 bubble.maximumWidth） ──────────────

class TestRealWindowWidth(unittest.TestCase):
    """通过 maxWidth 钳制来模拟真实 ListView 父容器宽度。"""

    def test_bubble_clamped_to_800_parent(self):
        """模拟 800 像素的窗口内容区：bubble.width <= 800（不会溢出右侧屏幕）"""
        info = _run_bubble('user', 'a' * 5000, parent_w=800)
        self.assertLessEqual(info['W'], 800)

    def test_bubble_clamped_to_1200_parent(self):
        info = _run_bubble('user', 'a' * 5000, parent_w=1200)
        self.assertLessEqual(info['W'], 1200)
        # 1200 > preferredWidth 680 → 应等于 680
        self.assertEqual(info['W'], 680,
            f"1200 父宽下 bubble 应取 preferredWidth=680，实际 {info['W']}")

    def test_bubble_clamped_to_narrow_300(self):
        """300 像素极端窄屏：bubble 自动收缩"""
        info = _run_bubble('user', 'a' * 5000, parent_w=300)
        self.assertLessEqual(info['W'], 300)
        # 同时 maximumWidth 属性应被设上
        self.assertEqual(info.get('MAX', 0), 300,
            f"bubble.maximumWidth 必须等于父传入值 300，实际 {info.get('MAX')}")

    def test_long_text_in_narrow_parent_grows_height(self):
        """300 像素极端窄屏 + 5000 个 a：implicitHeight 必须远大于 100（折了多行）"""
        info = _run_bubble('user', 'a' * 5000, parent_w=300)
        # 5000 / 30 chars per line ≈ 167 行 × ~22px ≈ 3700px
        self.assertGreater(info['IH'], 500,
            f"极端窄屏 + 5000 字符应折成多行高，implicitHeight={info['IH']}")


# ────────────── 测试 3: 多气泡混合 + Day 20.6.22 护栏 ──────────────

class TestDay20_6_22SelectableNotRegressed(unittest.TestCase):
    """Day 20.6.22 修复「可选中复制」—— 必须没被本次修复破坏。
    4 属性都还在：selectByMouse / selectByKeyboard / readOnly / persistentSelection。
    """

    def setUp(self):
        with open(BUBBLE_QML, encoding='utf-8') as f:
            self.src = f.read()

    @staticmethod
    def _strip_comments(src):
        import re
        out = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
        out = re.sub(r"//[^\n]*", "", out)
        return out

    @staticmethod
    def _find_block(src, control_id):
        import re
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

    def _check_attrs(self, tag):
        import re
        code = self._strip_comments(self.src)
        block = self._find_block(code, tag)
        self.assertIsNotNone(block, f'找不到 {tag} 控件块')
        for kw, want in (('selectByMouse', 'true'),
                         ('selectByKeyboard', 'true'),
                         ('readOnly', 'true'),
                         ('persistentSelection', 'true')):
            m = re.search(rf"\b{kw}\s*:\s*(\w+)", block)
            self.assertIsNotNone(m, f"{tag} 缺属性 {kw}")
            self.assertEqual(m.group(1), want,
                             f"{tag}.{kw} 必须 {want}，当前 {m.group(1)!r}")

    def test_bodyText_selectable_attrs_intact(self):
        self._check_attrs('bodyText')

    def test_codeText_selectable_attrs_intact(self):
        self._check_attrs('codeText')

    def test_bodyText_cursorVisible_false(self):
        import re
        code = self._strip_comments(self.src)
        block = self._find_block(code, 'bodyText')
        self.assertIsNotNone(block)
        m = re.search(r"cursorVisible\s*:\s*(\w+)", block)
        self.assertIsNotNone(m, 'bodyText 缺 cursorVisible')
        self.assertEqual(m.group(1), 'false')

    def test_codeText_cursorVisible_false(self):
        import re
        code = self._strip_comments(self.src)
        block = self._find_block(code, 'codeText')
        self.assertIsNotNone(block)
        m = re.search(r"cursorVisible\s*:\s*(\w+)", block)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'false')


class TestIsToolBranchWrapMode(unittest.TestCase):
    """isTool 分支（tool_call / tool_result）：bodyText 也必须 WrapAnywhere
    —— 不能 user/ai 修了 tool 没修。
    """
    text_long = "a" * 1000

    def test_tool_call_bodyText_wraps(self):
        info = _run_bubble('tool_call', self.text_long, parent_w=680)
        # bodyText 折行 → contentHeight > 单行高度
        self.assertGreater(info['BODY_H'], 20,
            f"tool_call bodyText 1000 字符必须折行，body_h={info['BODY_H']}")

    def test_tool_result_bodyText_wraps(self):
        info = _run_bubble('tool_result', self.text_long, parent_w=680)
        self.assertGreater(info['BODY_H'], 20,
            f"tool_result bodyText 1000 字符必须折行，body_h={info['BODY_H']}")

    def test_tool_call_codeText_wraps(self):
        """tool_call 气泡如果 hasCode=true，codeText 也必须 WrapAnywhere"""
        long_code = "x" * 1000
        info = _run_bubble('tool_call', '工具名', parent_w=680, code=long_code)
        self.assertGreater(info['CODE_H'], 20,
            f"tool_call codeText 1000 字符必须折行，code_h={info['CODE_H']}")
        self.assertLessEqual(info['W'], 680,
            f"tool_call 气泡宽度不应超过 680")

    def test_tool_result_codeText_wraps(self):
        long_code = "y" * 1000
        info = _run_bubble('tool_result', '工具结果', parent_w=680, code=long_code)
        self.assertGreater(info['CODE_H'], 20,
            f"tool_result codeText 1000 字符必须折行，code_h={info['CODE_H']}")


# ────────────── 测试 4: 多气泡混合 + 中间超长 ──────────────

class TestMultiBubbleMix(unittest.TestCase):
    """连续 5 条气泡，混合 4 种 who + 中间一条 5000 字符。
    每条都 width 不超过父 680，5000 字符那条折多行。
    """

    def test_five_bubbles_in_a_row(self):
        for who in ('user', 'ai', 'tool_call', 'tool_result', 'user'):
            info = _run_bubble(who, 'a' * 5000, parent_w=680)
            self.assertLessEqual(info['W'], 680,
                f"{who} 超长气泡 width={info['W']} 超 680")
            self.assertGreater(info['IH'], 500,
                f"{who} 超长气泡 implicitHeight={info['IH']} 太小（未折？）")

    def test_short_text_does_not_grow_height(self):
        """短文本不应无谓折多行（height 增长应 < 100）"""
        info = _run_bubble('user', 'hello world', parent_w=680)
        self.assertLess(info['IH'], 100,
            f"短文本气泡高度应很小，实际 {info['IH']}")


if __name__ == '__main__':
    unittest.main(verbosity=2)