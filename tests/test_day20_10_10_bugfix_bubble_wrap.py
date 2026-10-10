# -*- coding: utf-8 -*-
"""Day 20.10.10 B2: 「超长输入（无空格长串 / URL / 连续英数）气泡右侧超出屏幕」护栏。

复现：
- 输入框粘贴一段没有空格 / 没有中文标点的长英数字符串
  （如 'aaaa...×200' / 长 URL / 长 base64）
- 发送 → AI 回复或 user 气泡右侧一部分看不到

根因（静态扫描 + 历史 commit 还原）：
1) MessageBubble.qml：property int preferredWidth: 680 + implicitWidth: preferredWidth
   + 无 maximumWidth 上限 → 窄屏（窗口 < 680+padding）下 bubble 右边被截断。
2) bodyText 的 wrapMode: TextEdit.Wrap 只在空白 / 标点折行；遇到超长无
   空格串（'aaa...' / 'https://very-long...' / base64）会一直撑宽。
3) Main.qml 父 RowLayout：MessageBubble { Layout.preferredWidth: 680 }
   硬编码 preferredWidth + 无 maximumWidth + 两侧 Item fillWidth，
   bubble 实际宽度不受父容器宽度约束。

修复后必须钉死的行为：
1) MessageBubble.preferredWidth 仍 680（理想宽度）
2) MessageBubble 新增 maximumWidth 属性；width = min(preferred, maximum)
3) MessageBubble.bodyText 的 wrapMode 改为 TextEdit.WrapAnywhere
   （字符边界强制折行，防 'aaaa...' 溢出）
4) codeText 同样改 WrapAnywhere（防长 JSON / URL / base64 溢出）
5) Main.qml 父 delegate 给 MessageBubble 同时设 Layout.preferredWidth + Layout.maximumWidth，
   且显式赋值 maximumWidth: Math.min(...)
6) offscreen QML 加载一个装满超长串的 messageModel，断言：
   - 三种窗口宽度（800 / 1200 / 全屏）下 bubble.width 都不超过其父 Layout 可用宽度
   - bubble.width 在窄屏（800）下 < 680（自动收缩），在宽屏下稳定为 680

⚠️ offscreen 平台不能拿到真实像素，但 QQuickItem.width 是逻辑像素，QQmlApplicationEngine
   加载 + 短事件循环就能取到真实 width —— 不依赖 paint。
"""
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUBBLE_QML = os.path.join(_ROOT, 'qwen_app', 'qml', 'MessageBubble.qml')
MAIN_QML = os.path.join(_ROOT, 'qwen_app', 'qml', 'Main.qml')


# ────────────── 静态契约 ──────────────

def _strip_qml_comments(src):
    """去 QML 注释，避免关键字命中注释里的旧版描述"""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    out = re.sub(r"//[^\n]*", "", out)
    return out


def _find_block_for_id(src, control_id):
    """取 id=control_id 的控件块（花括号配对）"""
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


class TestBubbleStaticContract(unittest.TestCase):
    """MessageBubble.qml 静态契约 —— 拆 preferredWidth / maximumWidth，
    TextEdit 必须用 WrapAnywhere"""

    @classmethod
    def setUpClass(cls):
        with open(BUBBLE_QML, encoding='utf-8') as f:
            cls.src_raw = f.read()
        cls.src = _strip_qml_comments(cls.src_raw)

    def test_bubble_declares_maximumWidth_property(self):
        """Day 20.10.10 B2: 必须新增 maximumWidth 属性（默认 0 = 无约束）"""
        m = re.search(r'property\s+int\s+maximumWidth', self.src_raw)
        self.assertIsNotNone(m,
            'MessageBubble.qml 必须新增 property int maximumWidth（窄屏收缩开关）')

    def test_bubble_width_is_clamped_by_maximumWidth(self):
        """实际 width 必须取 min(preferredWidth, maximumWidth) —— 找 bubble 控件
        块的开头，验证 width: ... 表达式含 min(...) 或 Math.min(...)。"""
        block = _find_block_for_id(self.src, 'bubble')
        self.assertIsNotNone(block, '找不到 MessageBubble 根控件块')
        m = re.search(r'^\s*width\s*:\s*([^\n]+)', block, re.MULTILINE)
        self.assertIsNotNone(m, 'MessageBubble 必须显式声明 width（不能只靠 implicitWidth）')
        expr = m.group(1)
        self.assertTrue(
            'Math.min' in expr or 'min(' in expr,
            f'width 必须用 min(preferredWidth, maximumWidth) 钳制，当前表达式: {expr!r}')

    def test_preferredWidth_still_680_ideal_width(self):
        """preferredWidth 仍保留 680（理想宽度，宽屏下用这个）"""
        m = re.search(r'property\s+int\s+preferredWidth\s*:\s*(\d+)', self.src_raw)
        self.assertIsNotNone(m, 'preferredWidth 属性缺失')
        self.assertEqual(m.group(1), '680',
                         'preferredWidth 应保持 680（理想宽度，宽屏用）')

    def test_bodyText_uses_WrapAnywhere(self):
        """bodyText 必须用 TextEdit.WrapAnywhere（无空格长串也能折行）"""
        block = _find_block_for_id(self.src, 'bodyText')
        self.assertIsNotNone(block, '找不到 bodyText 控件块')
        m = re.search(r'wrapMode\s*:\s*(\S+)', block)
        self.assertIsNotNone(m, 'bodyText 缺 wrapMode')
        self.assertEqual(m.group(1), 'TextEdit.WrapAnywhere',
            f'bodyText.wrapMode 必须 TextEdit.WrapAnywhere（防长串/URL 溢出），当前 {m.group(1)!r}')

    def test_codeText_uses_WrapAnywhere(self):
        """codeText 也必须用 WrapAnywhere（长 JSON / 长 URL / 长 base64）"""
        block = _find_block_for_id(self.src, 'codeText')
        self.assertIsNotNone(block, '找不到 codeText 控件块')
        m = re.search(r'wrapMode\s*:\s*(\S+)', block)
        self.assertIsNotNone(m, 'codeText 缺 wrapMode')
        self.assertEqual(m.group(1), 'TextEdit.WrapAnywhere',
            f'codeText.wrapMode 必须 TextEdit.WrapAnywhere，当前 {m.group(1)!r}')

    def test_bodyText_keeps_selectable_attrs(self):
        """Day 20.10.10 B2 不能破坏 Day 20.6.22 修复（可选中复制）"""
        block = _find_block_for_id(self.src, 'bodyText')
        self.assertIsNotNone(block)
        for kw, want in (('selectByMouse', 'true'),
                         ('selectByKeyboard', 'true'),
                         ('readOnly', 'true'),
                         ('persistentSelection', 'true')):
            m = re.search(rf"\b{kw}\s*:\s*(\w+)", block)
            self.assertIsNotNone(m, f'bodyText 缺属性 {kw}')
            self.assertEqual(m.group(1), want)


class TestMainQmlDelegatePassesMaxWidth(unittest.TestCase):
    """Main.qml 的 MessageBubble delegate 必须传 maximumWidth（窄屏收缩开关）"""

    def setUp(self):
        with open(MAIN_QML, encoding='utf-8') as f:
            self.qml = f.read()

    def test_delegate_messagebubble_sets_layout_preferredWidth(self):
        """保留 Layout.preferredWidth: 680 —— 宽屏下气泡理想宽度"""
        # 在 MessageBubble { ... } delegate 块内找 Layout.preferredWidth
        m = re.search(r'MessageBubble\s*\{(.*?)\n\s*\}',
                      self.qml, re.DOTALL)
        self.assertIsNotNone(m, '找不到 MessageBubble delegate 块')
        body = m.group(1)
        self.assertRegex(body, r'Layout\.preferredWidth\s*:\s*680',
                         'Main.qml delegate 必须保留 Layout.preferredWidth: 680')

    def test_delegate_messagebubble_sets_layout_maximumWidth(self):
        """Day 20.10.10 B2: 必须新增 Layout.maximumWidth —— 真正开关"""
        m = re.search(r'MessageBubble\s*\{(.*?)\n\s*\}',
                      self.qml, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertRegex(body, r'Layout\.maximumWidth\s*:',
            'Main.qml delegate 必须显式设 Layout.maximumWidth（窄屏收缩）')

    def test_delegate_messagebubble_sets_maximumWidth_property(self):
        """同时给 bubble 的 maximumWidth 属性赋值（与 Layout.maximumWidth 双绑）"""
        m = re.search(r'MessageBubble\s*\{(.*?)\n\s*\}',
                      self.qml, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(1)
        # 排除掉 Layout.maximumWidth 后，bubble 自有属性 maximumWidth 必须也存在
        # 简化：找不在 'Layout.' 前缀后的 maximumWidth: ...
        plain_max = [ln.strip() for ln in body.splitlines()
                     if re.match(r'maximumWidth\s*:', ln.strip())]
        self.assertTrue(plain_max,
            'Main.qml delegate 必须显式赋值 bubble.maximumWidth 属性')


# ────────────── offscreen 真实加载 + width 断言 ──────────────

class TestBubbleWidthUnderOffscreen(unittest.TestCase):
    """offscreen 平台下用 QQmlApplicationEngine 真加载 MessageBubble.qml +
    不同窗口宽度（800 / 1200 / 1920），断言 bubble.width 不超过父可用宽度。"""

    # 用 QQuickView（轻量，只测 bubble 单元；不依赖 Main.qml 整套 ChatBridge
    # 路径 —— Main.qml 里有大量 forward ref / forward decl，单独跑 bubble 简单）
    _LOADER = r'''
import os, sys
sys.path.insert(0, r"{root}")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QUrl, QTimer, QMetaObject, Q_ARG, Q_RETURN_ARG
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlComponent, QQmlEngine

app = QGuiApplication(sys.argv)
# 320×240 起步；父宽度随后用 setWidth 调
engine = QQmlEngine()
qml_path = r"{qml}"
comp = QQmlComponent(engine)
# PyQt5 5.15: setData 接受 QByteArray（bytes 也可）；传 str 会 TypeError
with open(qml_path, "rb") as f:
    raw = f.read()
comp.setData(raw, QUrl.fromLocalFile(qml_path))
if comp.status() == QQmlComponent.Error:
    for e in comp.errors():
        print("QMLERROR:", e.toString(), flush=True)
    raise SystemExit(2)
obj = comp.beginCreate(engine.rootContext())
if obj is None:
    for e in comp.errors():
        print("QMLERROR:", e.toString(), flush=True)
    raise SystemExit(3)
# 模拟父 Layout 的可用宽度：直接给 bubble.maximumWidth 赋值
parent_width = {parent_w}
obj.setProperty("maximumWidth", parent_width)
obj.setProperty("text", "a" * 5000)  # 5000 个 a 的无空格长串
comp.completeCreate()
# 让 layout 一帧稳定
QTimer.singleShot(50, app.quit)
app.exec_()
print("PARENT_WIDTH=" + str(parent_width), flush=True)
print("BUBBLE_WIDTH=" + str(obj.property("width")), flush=True)
print("BUBBLE_IMPLICIT=" + str(obj.property("implicitWidth")), flush=True)
print("PREFERRED=" + str(obj.property("preferredWidth")), flush=True)
print("MAXIMUM=" + str(obj.property("maximumWidth")), flush=True)
print("BODY_HEIGHT=" + str(obj.property("implicitHeight")), flush=True)
'''

    def _run_offscreen_width(self, parent_width):
        code = self._LOADER.format(root=_ROOT, qml=BUBBLE_QML, parent_w=parent_width)
        env = dict(os.environ)
        env.setdefault('QT_QPA_PLATFORM', 'offscreen')
        p = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, env=env, cwd=_ROOT)
        if p.returncode != 0:
            self.fail(
                "offscreen QML 加载失败：\nstdout=%s\nstderr=%s"
                % (p.stdout[-1000:], p.stderr[-1500:]))
        out = {}
        for ln in (p.stdout or '').splitlines():
            if '=' in ln:
                k, _, v = ln.partition('=')
                out[k.strip()] = v.strip()
        for k in ('PARENT_WIDTH', 'BUBBLE_WIDTH', 'BUBBLE_IMPLICIT',
                  'PREFERRED', 'MAXIMUM'):
            self.assertIn(k, out,
                'offscreen 子进程没输出 %s，stdout=%s stderr=%s' %
                (k, p.stdout[-500:], p.stderr[-500:]))
        # PyQt 5.15: width 属性可能返回 float（real）类型，统一转 int
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return int(float(v))
        return {k: _to_int(out[k]) for k in ('PARENT_WIDTH', 'BUBBLE_WIDTH',
                                             'BUBBLE_IMPLICIT', 'PREFERRED', 'MAXIMUM')}

    def test_narrow_window_bubble_clamped_to_parent(self):
        """窗口/父可用 800：bubble.width 必须 ≤ 800（不能溢出）"""
        info = self._run_offscreen_width(800)
        self.assertLessEqual(info['BUBBLE_WIDTH'], 800,
            f"800 宽父容器下 bubble.width={info['BUBBLE_WIDTH']} 仍超 800")
        # 窄屏下 bubble.width 应自动收缩到 800-头部空间 = 800
        # （实际上 preferredWidth=680 在窄屏下也被父亲钳制 —— 期望 bubble.width < 680
        # 不对，680 < 800 所以等于 680；断言：bubble.width 应等于 preferredWidth=680）
        self.assertEqual(info['BUBBLE_WIDTH'], info['PREFERRED'],
            "800 宽屏（>680）：bubble.width 应取 preferredWidth=680（窄屏不变形）")

    def test_wide_window_bubble_capped_at_680(self):
        """父可用 1200：bubble.width 必须等于 680（不无限拉宽）"""
        info = self._run_offscreen_width(1200)
        self.assertLessEqual(info['BUBBLE_WIDTH'], 1200,
            f"1200 宽父容器下 bubble.width={info['BUBBLE_WIDTH']} 仍超 1200")
        self.assertEqual(info['BUBBLE_WIDTH'], info['PREFERRED'],
            "1200 宽屏：bubble.width 应取 preferredWidth=680（不无限拉宽）")

    def test_super_wide_window_bubble_capped_at_680(self):
        """父可用 1920：bubble.width 仍为 680（Math.min 钳制）"""
        info = self._run_offscreen_width(1920)
        self.assertLessEqual(info['BUBBLE_WIDTH'], 1920,
            f"1920 宽父容器下 bubble.width={info['BUBBLE_WIDTH']} 仍超 1920")
        self.assertEqual(info['BUBBLE_WIDTH'], info['PREFERRED'],
            "1920 宽屏：bubble.width 应取 preferredWidth=680（不无限拉宽）")

    def test_extreme_narrow_parent_does_not_underflow(self):
        """极端窄父（300）：bubble.width 必须 ≤ 300（不溢出右侧屏幕）

        注意：bubble.width = min(preferredWidth=680, maximumWidth=300) = 300。
        之前曾因 setProperty 后 width 绑定不重估而拿到 680.0 —— 修复方法是
        触发一次 widthChanged（force re-evaluation by polling），或者在
        setProperty 后立即读一下触发 layout pass。"""
        info = self._run_offscreen_width(300)
        # 窄父 300 < preferredWidth 680 → width 钳制到 300
        self.assertLessEqual(info['BUBBLE_WIDTH'], 300,
            f"300 宽父容器下 bubble.width={info['BUBBLE_WIDTH']} 仍超 300")
        # 极端窄屏：bubble 自动收缩到 300（不一定精确等于，因为某些 binding 组合下
        # 数字可能是 300 / 299 / 298 —— 取 ±5 像素容差，但不超过 300）
        self.assertLessEqual(info['BUBBLE_WIDTH'], 300,
            "300 极端窄屏：bubble.width 必须 ≤ 300（不允许溢出父容器）")
        # 同时验证 maximumWidth 已被父注入
        self.assertEqual(info['MAXIMUM'], 300,
            "bubble.maximumWidth 必须等于父传入值 300")

    def test_long_text_triggers_wrap_so_height_grows(self):
        """5000 个 'a' 的无空格长串 + 680 宽父：implicitHeight 必须远大于 50",
        只换行而不溢出。"""
        info = self._run_offscreen_width(680)
        # 至少要折行 5 次以上（5000/680 ≈ 7.4 行），每行 ~18px → implicitHeight > 50
        self.assertGreater(info.get('BUBBLE_IMPLICIT', 0) or
                           getattr(self, '_bh', 0), 0, 'implicitHeight 未捕获')
        # 单独跑一行：取 BODY_HEIGHT
        # 复用 _LOADER 但额外打印 implicitHeight
        # 已经在上面 _LOADER 里打印 BODY_HEIGHT 了
        # 修正：上面的 loader 打印的是 implicitHeight（bubbple 自有的）
        # 5000 个 a 折 8 行（每 ~80px），每行 ~18px 字号 → implicitHeight ~ 24 + 8*22 ≈ 200
        # 但 bubble 测的是自身 implicitHeight，含 padding 24。我们应该跑一次重拿：
        code = self._LOADER.format(root=_ROOT, qml=BUBBLE_QML, parent_w=680)
        env = dict(os.environ)
        env.setdefault('QT_QPA_PLATFORM', 'offscreen')
        p = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, env=env, cwd=_ROOT)
        body_h = 0
        for ln in (p.stdout or '').splitlines():
            if ln.startswith('BODY_HEIGHT='):
                raw = ln.split('=', 1)[1]
                try:
                    body_h = int(raw)
                except (TypeError, ValueError):
                    body_h = int(float(raw))
        self.assertGreater(body_h, 50,
            f"5000 个 'a' 在 680 宽下应至少折 3 行，body implicitHeight={body_h}，太小说明未折行")


if __name__ == '__main__':
    unittest.main(verbosity=2)