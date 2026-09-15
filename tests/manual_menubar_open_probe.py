# -*- coding: utf-8 -*-
"""Day 20.6.4 手动探针：MenuBar 点击后下拉菜单到底开没开（不进 unittest）。

用法：
    .venv/Scripts/python.exe tests/manual_menubar_open_probe.py real
默认 offscreen（布局断言可用，但真实鼠标路径/弹窗行为以 real 为准）。

流程：
  1. dump 每个 Menu 的初始 implicitWidth / count / item 宽度
  2. 找到所有 MenuBarItem，逐个 QTest 真点击
  3. 读对应 Menu 的 opened / width / implicitWidth，断言宽度能容纳最宽菜单项
  4. 点主窗口空白处关菜单（setProperty('opened',False) 关不掉真 Popup）
  5. 截图落 tests/artifacts/，全部输出 tee 到 tests/artifacts/probe_log.txt
"""
import os
import sys

_plat = sys.argv[1] if len(sys.argv) > 1 else 'offscreen'
if _plat != 'real':
    os.environ['QT_QPA_PLATFORM'] = _plat
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.stdout.reconfigure(line_buffering=True)

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'artifacts')
os.makedirs(ART, exist_ok=True)

# PowerShell 环境抓不到原生 stdout → 探针自带 tee 落盘（stdout + stderr 都收）
_log = open(os.path.join(ART, 'probe_log.txt'), 'w', encoding='utf-8')


class _Tee:
    def __init__(self, *f): self.f = f
    def write(self, s):
        for x in self.f: x.write(s)
    def flush(self):
        for x in self.f: x.flush()


sys.stdout = _Tee(sys.__stdout__, _log)
sys.stderr = _Tee(sys.__stderr__, _log)

import warnings
warnings.filterwarnings('ignore')

from PyQt5.QtCore import QUrl, QTimer, QPointF, Qt, QPoint
from PyQt5.QtWidgets import QApplication
from PyQt5.QtQml import QQmlApplicationEngine
from PyQt5.QtQuick import QQuickItem
from PyQt5.QtTest import QTest

app = QApplication(sys.argv)
from qwen_app.chat_bridge import ChatBridge

bridge = ChatBridge(theme='light')
engine = QQmlApplicationEngine()
qml_dir = os.path.join(PROJ, 'qwen_app', 'qml')
engine.addImportPath(qml_dir)
engine.rootContext().setContextProperty('bridge', bridge)
engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, 'Main.qml')))
win = engine.rootObjects()[0]
ROOT = win.contentItem()
FAIL = []

THEME = os.environ.get('PROBE_THEME', 'light')
if THEME != 'light':
    bridge.set_theme(THEME)


def vis_all(item, pred, out):
    try:
        kids = item.childItems()
    except Exception:
        kids = []
    for ch in kids:
        if pred(ch):
            out.append(ch)
        vis_all(ch, pred, out)


def obj_all(root, cls, out, depth=0):
    if depth > 80:
        return
    try:
        kids = root.children()
    except Exception:
        kids = []
    for ch in kids:
        try:
            if cls in ch.metaObject().className():
                out.append(ch)
        except Exception:
            continue
        obj_all(ch, cls, out, depth + 1)


def check(label, ok):
    print(('   OK   ' if ok else '   FAIL ') + label)
    if not ok:
        FAIL.append(label)


def menubar_items():
    out = []
    vis_all(ROOT, lambda i: 'MenuBarItem' in i.metaObject().className(), out)
    return out


def menus():
    got = []
    obj_all(win, 'Menu', got)
    real = []
    for m in got:
        t = m.property('title')
        if t:
            real.append((t, m))
    return real


def menu_dump(m, tag):
    iw = m.property('implicitWidth')
    w = m.property('width') or 0
    cnt = int(m.property('count') or 0)
    iws = []
    for i in range(cnt):
        it = m.itemAt(i)
        iws.append(round(it.property('implicitWidth') or 0) if it is not None else None)
    print(f'   [{tag}] implicitWidth={iw} width={w:.0f} count={cnt} item_iws={iws}')


STEPS = []


def stage_find():
    print(f'=== theme={THEME} 平台={_plat} ===')
    mbis = menubar_items()
    print(f'   MenuBarItem 数量 = {len(mbis)}')
    for i, b in enumerate(mbis):
        print(f'   [{i}] text={b.property("text")!r} '
              f'enabled={b.property("enabled")} visible={b.property("visible")} '
              f'scene={b.mapToScene(QPointF(0, 0)).x():.0f},{b.mapToScene(QPointF(0, 0)).y():.0f} '
              f'{b.property("width"):.0f}x{b.property("height"):.0f}')
    check('MenuBarItem 至少 1 个', len(mbis) > 0)
    for t, m in menus():
        menu_dump(m, f'初始 {t}')
    if not mbis:
        QTimer.singleShot(100, finish)
        return
    STEPS.extend(list(enumerate(mbis)))
    QTimer.singleShot(150, stage_next)


def stage_next():
    if not STEPS:
        QTimer.singleShot(100, finish)
        return
    idx, btn = STEPS.pop(0)
    txt = str(btn.property('text'))
    print(f'=== 点击 [{idx}] {txt!r} ===')
    c = btn.mapToScene(QPointF(btn.property('width') / 2, btn.property('height') / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
    QTimer.singleShot(400, lambda: stage_verify(idx, txt))


def stage_verify(idx, txt):
    key = txt.replace('&', '')
    found = None
    for t, m in menus():
        if key and key in str(t).replace('&', ''):
            found = m
            break
    if found is None:
        print(f'   !! 找不到 title 含 {key!r} 的 Menu')
        check(f'[{key}] 找到对应 Menu', False)
    else:
        opened = bool(found.property('opened'))
        w = found.property('width') or 0
        h = found.property('height') or 0
        # 最宽可见 item 的 implicitWidth（防文本被裁剪）
        widest = 0
        try:
            for i in range(int(found.property('count') or 0)):
                it = found.itemAt(i)
                if it is not None and it.property('visible'):
                    widest = max(widest, it.property('implicitWidth') or 0)
        except Exception:
            pass
        print(f'   opened={opened} x={found.property("x")} y={found.property("y")} '
              f'w={w:.0f} h={h:.0f} visible={found.property("visible")} widest_item={widest:.0f}')
        menu_dump(found, f'打开后 {key}')
        check(f'[{key}] 菜单已打开 (opened=True)', opened)
        check(f'[{key}] 菜单宽度 > 0（弹出窗口可见）', opened and w > 0)
        check(f'[{key}] 宽度容纳最宽菜单项（w >= widest）', opened and w >= widest)
        if opened:
            grab(f'_menubar_{idx}_{key}.png')
        # 点击主窗口空白处关闭（Esc 可能被弹窗键盘 grab 吞掉；
        # setProperty('opened', False) 关不掉真 Popup）
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(900, 500))
    QTimer.singleShot(350, stage_next)


def grab(name):
    win.grabWindow().save(os.path.join(ART, name))
    print('   [shot]', os.path.join('tests', 'artifacts', name))


def finish():
    print('=== 结果 ===')
    if FAIL:
        print(f'   {len(FAIL)} 项失败：')
        for f in FAIL:
            print('      -', f)
    else:
        print('   全部通过')
    QTimer.singleShot(100, app.quit)


QTimer.singleShot(700, stage_find)
QTimer.singleShot(25000, lambda: (print('!! TIMEOUT'), app.quit()))
app.exec_()
