# -*- coding: utf-8 -*-
"""Day 20.4.4 手动验证工具（不进 unittest 套件，文件名不是 test_*）。

真渲染 + 真点击（QTest.mouseClick 走完整 QtQuick 事件管线），验证：
  A. 气泡正文 / who 是否正确（防 QML 作用域遮蔽回归）
  B. 点会话条目能否切换 + 蓝色高亮是否跟随
  C. 侧边栏「⋯」三点按钮常显且能点开菜单
  D. 菜单是否贴在按钮附近（没飞到屏幕角落）
  E. 气泡「⋯」菜单同样能弹且定位正确

用法：
    .venv/Scripts/python.exe tests/manual_verify_qml_interactions.py real
会弹出一个真窗口（约 8 秒后自动关闭），截图落在 tests/artifacts/
（_v1_bubbles.png / _v2_switched.png / _v7_menu_final.png / _v8_bubble_menu.png）。
默认（不带参数）用 offscreen 平台，但 offscreen 不渲染 → ListView 不建 delegate，
所以 A~E 的多数断言会失败；要看真实结果必须传 real。
"""

import os
import sys

_plat = sys.argv[1] if len(sys.argv) > 1 else 'offscreen'
if _plat != 'real':
    os.environ['QT_QPA_PLATFORM'] = _plat
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.stdout.reconfigure(line_buffering=True)

# 截图统一落在 tests/artifacts/，别污染仓库根目录
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'artifacts')
os.makedirs(ART, exist_ok=True)

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
bridge.toast.connect(lambda m: print('   TOAST:', m))
engine = QQmlApplicationEngine()
qml_dir = os.path.join(PROJ, 'qwen_app', 'qml')
engine.addImportPath(qml_dir)
engine.rootContext().setContextProperty('bridge', bridge)
engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, 'Main.qml')))
win = engine.rootObjects()[0]
ROOT = win.contentItem()
FAIL = []


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
    """QObject children() 遍历（会话 Menu 用这个能找到）。"""
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


def find_menu_by_prop(prop):
    """在整棵树里找带某个属性的 Menu（sessionMenu / bubbleMenu）。"""
    for root in [win] + cls_all(ROOT, 'MessageBubble'):
        got = []
        obj_all(root, 'Menu', got)
        for m in got:
            try:
                if m.property(prop) is not None:
                    return m
            except Exception:
                continue
    return None


def cls_all(item, cls):
    out = []
    vis_all(item, lambda i: cls in i.metaObject().className(), out)
    return out


def grab(name):
    win.grabWindow().save(os.path.join(ART, name))
    print('   [shot]', os.path.join('tests', 'artifacts', name))


def conv_lv():
    for lv in cls_all(ROOT, 'QQuickListView'):
        if lv.property('count') == len(bridge.list_sessions()):
            return lv


def more_btns():
    lv = conv_lv()
    deps = lv.property('contentItem').childItems()
    btns = []
    for row in deps:
        vis_all(row, lambda i: i.metaObject().className() == 'QQuickRectangle'
                 and abs(i.property('width') - 24) < 0.6
                 and abs(i.property('height') - 24) < 0.6, btns)
    return deps, btns


def session_menu():
    return find_menu_by_prop('currentConvId')


def check(label, ok):
    print(('   OK   ' if ok else '   FAIL ') + label)
    if not ok:
        FAIL.append(label)


def stage_a():
    print('=== A. 气泡正文 ===')
    # 注意：真实发送路径会先过 ChatBridge._render_for_qml（user=html.escape，
    # ai=markdown_to_html）再 emit；这里必须走同一个函数，否则用原始字符串直接
    # emit 会被 RichText 把 <stdio.h> 当标签吃掉，截图就不代表真实行为了。
    R = ChatBridge._render_for_qml
    bridge.messageAdded.emit('user', {'text': R('user', 'use <stdio.h> & "q"', 'light'),
                                      'ts': '12:00', 'code': ''})
    bridge.messageAdded.emit('ai', {'text': R('ai', '**bold** 回答', 'light'),
                                    'ts': '12:01', 'code': ''})
    bridge.messageAdded.emit('tool_call', {'text': 'web_fetch', 'ts': '12:02',
                                           'code': R('tool_call', '{"url":"x"}', 'light')})
    bridge.messageAdded.emit('ai', {'text': R('ai', '结束', 'light'),
                                    'ts': '12:03', 'code': ''})
    QTimer.singleShot(700, stage_a2)


def stage_a2():
    bubs = cls_all(ROOT, 'MessageBubble')
    exp = [('user', 'use'), ('ai', 'bold'), ('tool_call', 'web_fetch'), ('ai', '结束')]
    for i, b in enumerate(bubs):
        if i >= len(exp):
            break
        who, txt = b.property('who'), str(b.property('text'))
        print(f'   [{i}] who={who!r} text={txt[:36]!r}')
        check(f'气泡[{i}] who={exp[i][0]}', who == exp[i][0])
        check(f'气泡[{i}] 有正文', len(txt) > 0)
    grab('_v1_bubbles.png')
    QTimer.singleShot(150, stage_b)


def stage_b():
    print('=== B. 点会话条目切换 ===')
    lv = conv_lv()
    deps = lv.property('contentItem').childItems()
    sessions = bridge.list_sessions()
    idx = next(i for i, s in enumerate(sessions[:10]) if not s['sel'] and i >= 3)
    tgt = sessions[idx]
    row = deps[idx]
    p = row.mapToScene(QPointF(row.property('width') / 2, row.property('height') / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
    QTimer.singleShot(400, lambda: stage_b2(tgt))


def stage_b2(tgt):
    check('切换后 _current_conv_id == 目标', bridge._current_conv_id == tgt['id'])
    sel = [s['name'] for s in bridge.list_sessions() if s['sel']]
    check('蓝色高亮跟到目标行', sel == [tgt['name']])
    grab('_v2_switched.png')
    # 等 ListView 重建 delegate 稳定后再测三点（否则可能拿到正在回收的 delegate）
    QTimer.singleShot(2000, stage_c)


def stage_c():
    print('=== C. 点「⋯」三点 ===')
    deps, btns = more_btns()
    lv = conv_lv()
    ltl = lv.mapToScene(QPointF(0, 0))
    lrect = (ltl.x(), ltl.y(), lv.property('width'), lv.property('height'))

    def in_view(b):
        t = b.mapToScene(QPointF(0, 0))
        return (lrect[0] <= t.x() <= lrect[0] + lrect[2]
                and lrect[1] <= t.y() <= lrect[1] + lrect[3])

    vis = [b for b in btns if b.property('visible') and in_view(b)]
    print(f'   moreBtn 共 {len(btns)}，常显 {len([b for b in btns if b.property("visible")])}，'
          f'在可视区内 {len(vis)}，delegate {len(deps)}')
    check('moreBtn 常显（不再依赖 hover）', len(btns) > 0
          and all(b.property('visible') for b in btns))
    check('有落在可视区内的 moreBtn', len(vis) > 0)
    sess = session_menu()
    check('找到 sessionMenu', sess is not None)
    if not vis or sess is None:
        finish()
        return
    btn = vis[0]
    p = btn.mapToScene(QPointF(btn.property('width') / 2, btn.property('height') / 2))
    bx, by = btn.mapToScene(QPointF(0, 0)).x(), btn.mapToScene(QPointF(0, 0)).y()
    print(f'   按钮 scene 左上 = ({bx:.0f},{by:.0f})，点击 ({(p.x()):.0f},{(p.y()):.0f})')
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
    QTimer.singleShot(500, lambda: stage_d(sess, bx, by))


def stage_d(sess, bx, by):
    print('=== D. 菜单 ===')
    par = sess.property('parent')
    print(f'   sessionMenu.parent = {par.metaObject().className() if par else None}')
    print(f'   sessionMenu.x,y = {sess.property("x")}, {sess.property("y")}'
          f'  w,h = {sess.property("width")}, {sess.property("height")}')
    check('三点菜单已弹出 (opened=True)', bool(sess.property('opened')))
    check('currentConvId 已写入', bool(sess.property('currentConvId')))
    items = cls_all(ROOT, 'PopupItem')
    ok_pos = False
    for it in items:
        tl = it.mapToScene(QPointF(0, 0))
        w, h = it.property('width'), it.property('height')
        print(f'   PopupItem scene 左上 = ({tl.x():.0f},{tl.y():.0f}) {w:.0f}x{h:.0f}'
              f'  右边缘={tl.x() + w:.0f}（按钮右边缘={bx + 24:.0f}）')
        # 必须落在侧边栏那一列里，且纵向在窗口内 —— 不是飞到角落
        if 0 <= tl.x() <= 300 and 40 <= tl.y() <= 760 - h and abs(tl.x() + w - (bx + 24)) < 30:
            ok_pos = True
    check('菜单贴在按钮下方（没飞到屏幕角落）', ok_pos)
    grab('_v7_menu_final.png')
    sess.setProperty('opened', False)
    QTimer.singleShot(250, stage_e)


def stage_e():
    print('=== E. 气泡三点菜单 ===')
    # stage B 切到的会话是空的 → messageModel 被清空，先补两条消息
    bridge.messageAdded.emit('user', {'text': 'hello', 'ts': '12:10', 'code': ''})
    bridge.messageAdded.emit('ai', {'text': 'world 回答', 'ts': '12:11', 'code': ''})
    QTimer.singleShot(700, stage_e_b)


def stage_e_b():
    bubs = cls_all(ROOT, 'MessageBubble')
    print('   MessageBubble =', len(bubs))
    btns = []
    for b in bubs:
        vis_all(b, lambda i: i.metaObject().className() == 'QQuickRectangle'
                 and abs(i.property('width') - 22) < 0.6
                 and abs(i.property('height') - 22) < 0.6, btns)
    print(f'   22x22 气泡 moreBtn = {len(btns)}（应等于 {len(bubs)}）')
    check('每个气泡都有三点按钮', len(btns) == len(bubs) and len(btns) > 0)
    menu = find_menu_by_prop('currentMsgText')
    check('找到 bubbleMenu', menu is not None)
    if not btns or menu is None:
        finish()
        return
    btn = btns[0]
    bx = btn.mapToScene(QPointF(0, 0)).x()
    p = btn.mapToScene(QPointF(11, 11))
    print(f'   点气泡按钮 scene=({p.x():.0f},{p.y():.0f})')
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
    QTimer.singleShot(500, lambda: stage_e2(menu, bx))


def stage_e2(menu, bx):
    check('气泡菜单已弹出 (opened=True)', bool(menu.property('opened')))
    check('气泡菜单数据已写入', bool(menu.property('currentMsgWho')))
    ok = False
    for it in cls_all(ROOT, 'PopupItem'):
        tl = it.mapToScene(QPointF(0, 0))
        w, h = it.property('width'), it.property('height')
        print(f'   PopupItem ({tl.x():.0f},{tl.y():.0f}) {w:.0f}x{h:.0f}')
        if abs(tl.x() + w - (bx + 22)) < 60:
            ok = True
    check('气泡菜单贴在按钮附近', ok)
    grab('_v8_bubble_menu.png')
    finish()


def finish():
    print('=== 结果 ===')
    if FAIL:
        print(f'   {len(FAIL)} 项失败：')
        for f in FAIL:
            print('      -', f)
    else:
        print('   全部通过')
    QTimer.singleShot(100, app.quit)


QTimer.singleShot(700, stage_a)
QTimer.singleShot(25000, lambda: (print('!! TIMEOUT'), app.quit()))
app.exec_()
