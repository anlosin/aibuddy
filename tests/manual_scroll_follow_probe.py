# -*- coding: utf-8 -*-
"""Day 20.6.5 手动探针：消息列表流式跟随滚动（不进 unittest）。

用法：
    .venv/Scripts/python.exe tests/manual_scroll_follow_probe.py real

模拟完整对话流（走 bridge 真信号，QML 走真实渲染布局）：
  A. 用户消息 + AI 占位 → 视口应在底部
  B. 流式 appendToLast 长内容 → 视口应跟随气泡长高
  C. 用户真拖拽上滚（QTest）→ 解除跟随，后续流式不拽人
  D. messageReplaced（高度突变）→ 未跟随时 follow 态不变、不强制回底
  E. 拖回底部恢复跟随 → 新一轮对话全程跟随，用户问题在上、回答在下
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

_log = open(os.path.join(ART, 'scroll_probe_log.txt'), 'w', encoding='utf-8', buffering=1)


class _Tee:
    def __init__(self, *f): self.f = f
    def write(self, s):
        for x in self.f: x.write(s)
    def flush(self):
        for x in self.f: x.flush()


sys.stdout = _Tee(sys.__stdout__, _log)
sys.stderr = _Tee(sys.__stderr__, _log)

# 原生崩溃（0xC0000005 等）走 fd 2，Python 级 Tee 抓不到 → dup2 到文件
import faulthandler
_fd_log = os.open(os.path.join(ART, 'scroll_fd_log.txt'),
                  os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
faulthandler.enable(_fd_log)
os.dup2(_fd_log, 2)

import warnings
warnings.filterwarnings('ignore')

from PyQt5.QtCore import QUrl, QTimer, QPoint, Qt
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


def vis_all(item, pred, out):
    try:
        kids = item.childItems()
    except Exception:
        kids = []
    for ch in kids:
        if pred(ch):
            out.append(ch)
        vis_all(ch, pred, out)


def check(label, ok, extra=''):
    print(('   OK   ' if ok else '   FAIL ') + label + (f'  ({extra})' if extra else ''))
    if not ok:
        FAIL.append(label)


def grab(name):
    win.grabWindow().save(os.path.join(ART, name))
    print('   [shot]', os.path.join('tests', 'artifacts', name))


def msg_list():
    out = []
    vis_all(ROOT, lambda i: 'QQuickListView' in i.metaObject().className(), out)
    for lv in out:
        try:
            if abs((lv.property('leftMargin') or 0) - 80) < 0.5:
                return lv
        except Exception:
            continue
    return None


ML = msg_list()


def content_bottom():
    """contentItem 子项的真实底边（contentHeight 属性会被 originY 记账抬高，
    不能用来判断到底 —— 探针实测 contentH=5946 而真实内容底 3078）。"""
    ci = ML.property('contentItem')
    bottom = 0
    for d in ci.childItems():
        bottom = max(bottom, (d.property('y') or 0) + (d.property('height') or 0))
    return bottom


def at_bottom(slack=6):
    b = content_bottom()
    h = ML.property('height') or 0
    cy = ML.property('contentY') or 0
    return cy + h >= b - slack, f'contentY={cy:.0f} h={h:.0f} contentBottom={b:.0f}'


def delegates():
    ci = ML.property('contentItem')
    return ci.childItems() if ci else []


def emit_user(text, ts):
    bridge.messageAdded.emit('user', bridge._mk_msg(text, ts))


def drag(on_win, x0, y0, x1, y1, steps=12, delay=25):
    """真拖拽（走 Flickable 手势管线：movementStarted/Ended 会真实触发）。"""
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y0))
    for i in range(1, steps + 1):
        xi = x0 + (x1 - x0) * i // steps
        yi = y0 + (y1 - y0) * i // steps
        QTest.mouseMove(win, QPoint(xi, yi), delay)
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(x1, y1))


LONG = ('这是一段很长的流式回答。' * 60)


def stage_a():
    print(f'=== A. 用户消息 + AI 占位（平台={_plat}）===')
    try:
        emit_user('帮我写一段自我介绍', '10:00')
        bridge.messageAdded.emit('ai', bridge._mk_msg('', '10:00'))
    except Exception:
        import traceback
        traceback.print_exc()
        QTimer.singleShot(100, finish)
        return
    QTimer.singleShot(500, stage_a2)


def stage_a2():
    ok, info = at_bottom()
    print('   几何:', info, 'followBottom =', ML.property('followBottom'))
    check('A1 视口在底部（用户消息可见）', ok)
    QTimer.singleShot(150, stage_b)


def stage_b():
    print('=== B. 流式 appendToLast ×8 ===')
    for i in range(8):
        bridge.appendToLast.emit('ai', f'第{i}段。' + LONG)
    QTimer.singleShot(1500, stage_b2)


def stage_b2():
    ok, info = at_bottom()
    print('   几何:', info, 'followBottom =', ML.property('followBottom'))
    check('B1 流式过程中视口跟随到底部', ok)
    grab('_scroll_B_streaming.png')
    QTimer.singleShot(200, stage_c)


def stage_c():
    print('=== C. 用户拖拽上滚 → 解除跟随 ===')
    drag(win, 700, 150, 700, 430)          # 向下拖 = 看历史（contentY 减小）
    QTimer.singleShot(800, stage_c2)


def stage_c2():
    print('   followBottom =', ML.property('followBottom'))
    check('C1 拖拽后 followBottom=False', ML.property('followBottom') is False)
    cy0 = ML.property('contentY')
    b0 = content_bottom()
    h = ML.property('height')
    not_at_end = (cy0 + h) < (b0 - 100)
    bridge.appendToLast.emit('ai', '上滚期间追加的段落。' + LONG)
    QTimer.singleShot(700, lambda: stage_c3(cy0, not_at_end))


def stage_c3(cy0, not_at_end):
    b1 = content_bottom()
    h = ML.property('height')
    cy1 = ML.property('contentY')
    print(f'   contentY: {cy0:.0f} -> {cy1:.0f}, contentBottom={b1:.0f}')
    check('C2 未跟随时流式不拽视口', not_at_end and (cy1 + h) < (b1 - 100))
    QTimer.singleShot(150, stage_d)


def stage_d():
    print('=== D. messageReplaced（HTML 高度突变，未跟随）===')
    bridge.messageReplaced.emit('ai', '<b>渲染后的长回答</b><br>' + ('段落内容。<br><br>' * 40))
    QTimer.singleShot(700, stage_d2)


def stage_d2():
    fb = ML.property('followBottom')
    print(f'   followBottom={fb} contentY={ML.property("contentY"):.0f} '
          f'contentBottom={content_bottom():.0f}')
    # 只验状态：替换后 followBottom 不得被强制改回 True。
    # （若替换使内容变矮，ListView 会钳制 contentY 到底边 —— 那是正常行为）
    check('D1 未跟随时替换不解除/强制跟随（仍 False）', fb is False)
    QTimer.singleShot(150, stage_e)


def stage_e():
    print('=== E. 拖回底部恢复跟随 → 新一轮对话 ===')
    drag(win, 700, 430, 700, 90)           # 向上拖 = 回到底部
    QTimer.singleShot(900, stage_e2)


def stage_e2():
    print('   followBottom =', ML.property('followBottom'))
    check('E1 拖回底部后 followBottom=True', ML.property('followBottom') is True)
    emit_user('第二个问题', '10:05')
    bridge.messageAdded.emit('ai', bridge._mk_msg('', '10:05'))
    QTimer.singleShot(250, stage_e3)


def stage_e3():
    for i in range(5):
        bridge.appendToLast.emit('ai', f'回答第{i}段。' + LONG)
    QTimer.singleShot(250, lambda: bridge.messageReplaced.emit(
        'ai', '<b>第二个问题的回答</b><br>' + ('详细内容。<br><br>' * 30)))
    QTimer.singleShot(1000, stage_f)


def stage_f():
    print('=== F. 最终位置校验 ===')
    ok, info = at_bottom()
    print('   几何:', info, 'followBottom =', ML.property('followBottom'),
          'count =', ML.property('count'))
    check('F1 替换后视口在底部', ok)
    # 顺序正确性靠截图目视（contentItem 里可能有 stale delegate，
    # 对它 mapToItem 会段错误 —— 不要遍历旧 childItems 做几何断言）
    grab('_scroll_F_final.png')
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


if ML is None:
    print('!! 找不到消息列表 msgList')
    QTimer.singleShot(50, finish)
else:
    QTimer.singleShot(700, stage_a)
QTimer.singleShot(40000, lambda: (print('!! TIMEOUT'), app.quit()))
app.exec_()
