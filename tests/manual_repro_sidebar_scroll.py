# -*- coding: utf-8 -*-
"""Day 20.4.5 复现 v3：用 ListView 官方 API 滚动（保证 delegate 正确绑定）。

v2 用 setProperty('contentY', ...) 直接赋值 → Qt 移动了 viewport 但**没有重新
绑定 delegate 的 index/model**（实测 delegate.y=11036 却 index=3、label=test-46），
说明那种滚动方式不是真实用户行为，会产生假象。v3 改用
QMetaObject.invokeMethod(lv, 'positionViewAtIndex', ...)，
并 dump 可见 delegate 的 (y, label) 校验绑定是否自洽。
"""
import os
import sys

plat = sys.argv[1] if len(sys.argv) > 1 else 'real'
if plat != 'real':
    os.environ['QT_QPA_PLATFORM'] = plat
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'artifacts')
os.makedirs(ART, exist_ok=True)

import warnings
warnings.filterwarnings('ignore')

from PyQt5.QtCore import QUrl, QTimer, QPointF, Qt, QPoint, QMetaObject, Q_ARG
from PyQt5.QtWidgets import QApplication
from PyQt5.QtQml import QQmlApplicationEngine
from PyQt5.QtQuick import QQuickItem
from PyQt5.QtTest import QTest

LOG = []
SIG = {'n': 0}


def log(*a):
    LOG.append(' '.join(str(x) for x in a))


app = QApplication(sys.argv)
from qwen_app.chat_bridge import ChatBridge

bridge = ChatBridge(theme='light')
bridge.sessionListChanged.connect(lambda: SIG.__setitem__('n', SIG['n'] + 1))
engine = QQmlApplicationEngine()
qml_dir = os.path.join(PROJ, 'qwen_app', 'qml')
engine.addImportPath(qml_dir)
engine.rootContext().setContextProperty('bridge', bridge)
engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, 'Main.qml')))
win = engine.rootObjects()[0]
ROOT = win.contentItem()


def vis_all(item, pred, out):
    try:
        kids = item.childItems()
    except Exception:
        kids = []
    for ch in kids:
        if pred(ch):
            out.append(ch)
        vis_all(ch, pred, out)


def cls_all(item, cls):
    out = []
    vis_all(item, lambda i: cls in i.metaObject().className(), out)
    return out


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


def conv_lv():
    for lv in cls_all(ROOT, 'QQuickListView'):
        if lv.property('count') == len(bridge.list_sessions()):
            return lv


def geom(lv):
    t = lv.mapToScene(QPointF(0, 0))
    return t.x(), t.y(), lv.property('width'), lv.property('height')


def _texts(item):
    out = []
    vis_all(item, lambda i: i.metaObject().className() == 'QQuickText', out)
    return out


def _name_label(d):
    """取 delegate 里 font.pixelSize == 13 的那个 Text（会话名）。"""
    for t in _texts(d):
        try:
            f = t.property('font')
            if f is not None and abs(f.pixelSize() - 13) < 0.6:
                return str(t.property('text'))
        except Exception:
            continue
    return None


def visible_rows(lv):
    """返回 [(delegate_y_in_content, label)]，按 y 排序。"""
    rows = []
    for d in lv.property('contentItem').childItems():
        try:
            h = d.property('height')
        except Exception:
            continue
        if h is None or abs(float(h) - 60) > 1:
            continue
        lab = _name_label(d)
        if lab is not None:
            rows.append((float(d.property('y')), lab))
    rows.sort()
    return rows


def run():
    lv = conv_lv()
    sessions = bridge.list_sessions()
    log('sessions=%d current=%s' % (len(sessions), bridge._current_conv_id))
    # 滚到深处（旧代码就是在深层滚动时把菜单算到 y=0 的）
    QMetaObject.invokeMethod(lv, 'positionViewAtIndex', Qt.DirectConnection,
                             Q_ARG(int, 150), Q_ARG(int, 1))
    QTimer.singleShot(500, lambda: after_scroll(lv, bridge.list_sessions()))


def after_scroll(lv, sessions):
    cy, vy = lv.property('contentHeight'), lv.property('contentY')
    vx, vtop, vw, vh = geom(lv)
    log('contentHeight=%.0f contentY=%.0f view=(%.0f,%.0f %.0fx%.0f)'
        % (cy, vy, vx, vtop, vw, vh))
    rows = visible_rows(lv)
    log('可见 delegate (contentY=%.0f):' % vy)
    for dy, label in rows:
        midx = dy / 62
        log('   y=%5.0f  idx≈%5.1f  label=%r' % (dy, midx, label))
    # 自洽性校验
    bad = [(dy, lab) for dy, lab in rows
           if not (0 <= dy / 62 < len(sessions) and sessions[int(round(dy / 62))]['name'] == lab)]
    log('绑定自洽: %s（不一致 %d 行）' % ('OK' if not bad else 'BAD', len(bad)))
    # 选一个可见的非当前行
    target = None
    for dy, label in rows:
        i = int(round(dy / 62))
        if 0 <= i < len(sessions) and not sessions[i]['sel']:
            row_view_top = dy - vy
            if 30 <= row_view_top <= vh - 70:
                target = i
                break
    if target is None:
        log('!! 无可点行')
        return finish()
    row_view_top = target * 62 - vy
    log('目标 index=%d name=%r label=%r 视口top=%.0f'
        % (target, sessions[target]['name'],
           next((lab for dy, lab in rows if int(round(dy / 62)) == target), '?'), row_view_top))
    px, py = vx + 100, vtop + row_view_top + 30
    SIG['n'] = 0
    y_before = vy
    log('点击 scene=(%.0f,%.0f)' % (px, py))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(px), int(py)))
    for tag, ms in (('T+150ms', 150), ('T+600ms', 600)):
        QTimer.singleShot(ms, lambda t=tag: log('  [%s] contentY=%.0f current=%s'
                                                % (t, lv.property('contentY'), bridge._current_conv_id)))
    QTimer.singleShot(1000, lambda: after_click(lv, sessions, target, y_before))


def after_click(lv, sessions, target, y_before):
    y_after = lv.property('contentY')
    log('--- 点完行 ---')
    log('contentY: %.0f -> %.0f' % (y_before, y_after))
    log('sessionListChanged 触发 %d 次' % SIG['n'])
    log('current=%s 期望=%s %s' % (bridge._current_conv_id, sessions[target]['id'],
                                  'OK' if bridge._current_conv_id == sessions[target]['id'] else 'MISMATCH'))
    log('  >>> %s' % ('BUG1 复现：列表跳回最顶！' if (y_before > 5 and y_after < 1) else 'BUG1 未复现'))
    win.grabWindow().save(os.path.join(ART, '_r1_after_switch.png'))
    QTimer.singleShot(200, lambda: step_dot(lv, sessions, target))


def find_btn_for_label(lv, label):
    """在可视 delegate 里按会话名找到对应的 24x24 ⋯ 按钮，返回 (btn, scene_topleft)。"""
    vx, vtop, vw, vh = geom(lv)
    for d in lv.property('contentItem').childItems():
        try:
            h = d.property('height')
        except Exception:
            continue
        if h is None or abs(float(h) - 60) > 1:
            continue
        if _name_label(d) != label:
            continue
        btns = []
        vis_all(d, lambda x: x.metaObject().className() == 'QQuickRectangle'
                and abs(x.property('width') - 24) < 0.6
                and abs(x.property('height') - 24) < 0.6, btns)
        if btns:
            b = btns[0]
            t = b.mapToScene(QPointF(0, 0))
            if vtop - 5 <= t.y() <= vtop + vh - 20:
                return b, (t.x(), t.y())
    return None, None


def step_dot(lv, sessions, target):
    log('--- 点 ⋯ 三点 ---')
    label = sessions[target]['name']
    btn, bt = find_btn_for_label(lv, label)
    if btn is None:
        log('!! 找不到 %r 对应的 ⋯ 按钮' % label)
        return finish()
    log('真实按钮 scene 左上=(%.0f,%.0f) 期望菜单右边缘≈%.0f 按钮下边缘≈%.0f'
        % (bt[0], bt[1], bt[0] + 24, bt[1] + 24))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(bt[0] + 12), int(bt[1] + 12)))
    QTimer.singleShot(600, lambda: check_menu(bt[0], bt[1]))


def check_menu(bx, by):
    menus = []
    obj_all(win, 'Menu', menus)
    for m in menus:
        try:
            if m.property('currentConvId') is not None and m.property('opened'):
                log('sessionMenu: x=%s y=%s w=%s h=%s' % (m.property('x'), m.property('y'),
                                                          m.property('width'), m.property('height')))
        except Exception:
            pass
    ok = False
    for it in cls_all(ROOT, 'PopupItem'):
        tl = it.mapToScene(QPointF(0, 0))
        w, h = it.property('width'), it.property('height')
        log('  菜单 scene=(%.0f,%.0f) %.0fx%.0f  右差=%+.0f 顶-按钮底=%+.0f'
            % (tl.x(), tl.y(), w, h, tl.x() + w - (bx + 24), tl.y() - (by + 24)))
        if abs(tl.x() + w - (bx + 24)) < 30 and -20 <= tl.y() - (by + 24) <= 80:
            ok = True
    log('  >>> 菜单位置%s' % ('正常' if ok else '有问题！'))
    win.grabWindow().save(os.path.join(ART, '_r2_menu_after_switch.png'))
    finish()


def finish():
    with open(os.path.join(ART, 'repro_report.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    QTimer.singleShot(100, app.quit)


QTimer.singleShot(900, run)
QTimer.singleShot(30000, lambda: (log('!! TIMEOUT'), finish()))
app.exec_()
