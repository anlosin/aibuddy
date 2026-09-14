# -*- coding: utf-8 -*-
"""Day 20.4.5 定向验证 2：用 flick()（用户手势）滚到深处后点会话，看 contentY 是否被复位。

前两次实验结论：
- positionViewAtIndex 滚到 9046 后点行 → contentY 保持（未复现）
- 直接调 refreshConvList() → contentY 保持（未复现）
差别可能在于：程序化定位会给 ListView 留一个 "anchor"（Qt 会在模型 reset 时
按 anchor 恢复），而用户手指/滚轮滚动是纯 contentY 驱动。这里用 flick() 模拟。
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


def _name(d):
    for t in _texts(d):
        try:
            f = t.property('font')
            if f is not None and abs(f.pixelSize() - 13) < 0.6:
                return str(t.property('text'))
        except Exception:
            continue
    return None


def rows(lv):
    out = []
    for d in lv.property('contentItem').childItems():
        try:
            h = d.property('height')
        except Exception:
            continue
        if h is None or abs(float(h) - 60) > 1:
            continue
        lab = _name(d)
        if lab is not None:
            out.append((float(d.property('y')), lab))
    out.sort()
    return out


def run():
    lv = conv_lv()
    log('sessions=%d current=%s' % (len(bridge.list_sessions()), bridge._current_conv_id))
    flick_once(lv, 0)


def flick_once(lv, n):
    """flick 一次后等惯性停止（否则下一次点击会被当成"停下 flick"而吞掉）。"""
    QMetaObject.invokeMethod(lv, 'flick', Qt.DirectConnection,
                             Q_ARG(float, 0.0), Q_ARG(float, -6000.0))
    QTimer.singleShot(1200, lambda: wait_idle(lv, n))


def wait_idle(lv, n, tries=0):
    if lv.property('flicking') and tries < 30:
        QTimer.singleShot(200, lambda: wait_idle(lv, n, tries + 1))
        return
    log('  flick#%d 停止: contentY=%.0f flicking=%s'
        % (n + 1, lv.property('contentY'), lv.property('flicking')))
    if n < 5 and lv.property('contentY') < lv.property('contentHeight') - lv.property('height') - 100:
        flick_once(lv, n + 1)
    else:
        check(lv)


def check(lv):
    vy = lv.property('contentY')
    log('flick 后 contentY=%.0f max=%.0f'
        % (vy, max(0.0, lv.property('contentHeight') - lv.property('height'))))
    rws = rows(lv)
    log('可见行数=%d' % len(rws))
    for dy, lab in rws[:5]:
        log('   y=%5.0f idx≈%5.1f %r' % (dy, dy / 62, lab))
    vx, vtop, vw, vh = geom(lv)
    tgt = None
    for dy, lab in rws:
        i = int(round(dy / 62))
        if 30 <= dy - vy <= vh - 70:
            tgt = (i, lab, dy)
            break
    if tgt is None:
        log('!! 无可点行')
        return done(lv, vy)
    i, lab, dy = tgt
    px, py = vx + 100, vtop + (dy - vy) + 30
    log('点击 index=%d label=%r scene=(%.0f,%.0f)' % (i, lab, px, py))
    SIG['n'] = 0
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(px), int(py)))
    QTimer.singleShot(150, lambda: log('  [T+150] contentY=%.0f current=%s'
                                       % (lv.property('contentY'), bridge._current_conv_id)))
    QTimer.singleShot(800, lambda: finish(lv, vy, i))


def finish(lv, vy, i):
    log('--- 点完 ---')
    log('contentY: %.0f -> %.0f' % (vy, lv.property('contentY')))
    log('sessionListChanged 触发 %d 次' % SIG['n'])
    log('current=%s' % bridge._current_conv_id)
    log('  >>> %s' % ('BUG1 复现！' if vy > 5 and lv.property('contentY') < 1 else 'BUG1 未复现'))
    win.grabWindow().save(os.path.join(ART, '_f1_after_flick_click.png'))
    done(lv, vy)


def done(lv, vy):
    with open(os.path.join(ART, 'flick_test.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    QTimer.singleShot(100, app.quit)


QTimer.singleShot(900, run)
QTimer.singleShot(25000, lambda: (log('!! TIMEOUT'), done(conv_lv(), 0)))
app.exec_()
