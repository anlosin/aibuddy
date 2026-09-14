# -*- coding: utf-8 -*-
"""Day 20.4.5 定向验证：convModel 全量重建(refreshConvList) 是否会复位 contentY。

这是 BUG1「点会话后左侧跳回最顶」的唯一嫌疑路径：load_session 会 emit
sessionListChanged → onSessionListChanged → refreshConvList() → convModel.clear()
+ 185 次 append() → ListView 模型 reset。

本脚本：先滚到深处，再直接调用 QML 的 refreshConvList()，看 contentY 是否归零。
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

from PyQt5.QtCore import QUrl, QTimer, QPointF, Qt, QMetaObject
from PyQt5.QtWidgets import QApplication
from PyQt5.QtQml import QQmlApplicationEngine
from PyQt5.QtQuick import QQuickItem

LOG = []


def log(*a):
    LOG.append(' '.join(str(x) for x in a))
    print(LOG[-1])


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


def run():
    lv = conv_lv()
    # 滚到深处（index 150 居中）
    from PyQt5.QtCore import Q_ARG
    QMetaObject.invokeMethod(lv, 'positionViewAtIndex', Qt.DirectConnection,
                             Q_ARG(int, 150), Q_ARG(int, 1))
    QTimer.singleShot(500, lambda: step2(lv))


def step2(lv):
    y0 = lv.property('contentY')
    log('滚到深处: contentY=%.0f contentHeight=%.0f count=%s'
        % (y0, lv.property('contentHeight'), lv.property('count')))
    ok = QMetaObject.invokeMethod(win, 'refreshConvList', Qt.DirectConnection)
    log('调用 refreshConvList() 返回=%s' % ok)
    QTimer.singleShot(200, lambda: sample(lv, y0, 'T+200ms'))
    QTimer.singleShot(700, lambda: sample(lv, y0, 'T+700ms'))
    QTimer.singleShot(1200, lambda: done(lv, y0))


def sample(lv, y0, tag):
    log('  [%s] contentY=%.0f  count=%s' % (tag, lv.property('contentY'), lv.property('count')))


def done(lv, y0):
    y1 = lv.property('contentY')
    log('=== 结论 ===')
    log('contentY: %.0f -> %.0f' % (y0, y1))
    log('  >>> %s' % ('refreshConvList 会复位 contentY → BUG1 根因确认'
                      if y0 > 5 and y1 < 1 else 'refreshConvList 未复位 contentY'))
    with open(os.path.join(ART, 'scroll_test.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    QTimer.singleShot(100, app.quit)


QTimer.singleShot(900, run)
QTimer.singleShot(20000, lambda: (log('!! TIMEOUT'), done(conv_lv(), 1)))
app.exec_()
