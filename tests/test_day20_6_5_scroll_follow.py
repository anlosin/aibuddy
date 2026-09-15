# -*- coding: utf-8 -*-
"""Day 20.6.5 护栏：消息列表流式跟随滚动。

用户报障：① AI 流式回答不自动滚动；② 生成完成后视图停在「用户问题压在
视口底部」的位置，Markdown 渲染后的回答全在屏幕外。

根因：onMessageAdded 的同步 positionViewAtEnd() 在内容高度更新前执行；
流式 appendToLast / Markdown messageReplaced（气泡高度突变）完全没有
滚动逻辑。

修复方案（真平台探针 manual_scroll_follow_probe.py 实测）：
- msgList.followBottom「粘底」标志，**只在用户手势时变更**
  （onMovementStarted 解除 / onMovementEnded 停在底部时恢复）
- 禁止用 onContentYChanged 瞬时比较 —— 气泡 Text 重布局是异步的，
  contentHeight 抖动 + contentY 钳制会把跟随态误关（探针实测卡死）
- contentHeight 属性会被 originY 记账抬高（实测 5946 vs 真实内容底
  3078），不能用 contentHeight 判断是否到底
- 滚动 = callLater 一帧后滚 + contentHeight 每次变化重滚 + 有界 Timer
  （80ms × 25）兜底；「到底就停」的 Timer 条件用的 contentHeight 与
  滚动时一样是过期值，会提前收工
"""
import os
import re
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QML = os.path.join(PROJ, 'qwen_app', 'qml')


def _strip_comments(src):
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("//"))


class TestScrollFollow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(QML, 'Main.qml'), encoding='utf-8') as f:
            cls.src = _strip_comments(f.read())
        m = re.search(r'id:\s*msgList[\s\S]*?delegate:', cls.src)
        self_fail = m is None
        cls.msglist_block = m.group(0) if m else ''
        # Connections 整块（onMessageAdded 等都在里面）
        m2 = re.search(r'Connections \{[\s\S]*?\n    \}', cls.src)
        cls.conn_block = m2.group(0) if m2 else ''

    def test_follow_state_machine(self):
        self.assertIn('property bool followBottom: true', self.msglist_block,
                      'msgList 必须有 followBottom 粘底标志')
        self.assertIn('onMovementStarted: followBottom = false', self.msglist_block,
                      '用户手势开始必须解除跟随')
        self.assertIn('onMovementEnded: followBottom = atYEnd', self.msglist_block,
                      '手势结束停在底部必须恢复跟随')

    def test_no_contenty_instant_check(self):
        self.assertNotIn('onContentYChanged', self.msglist_block,
                         '禁止用 onContentYChanged 瞬时比较维护 followBottom'
                         '（Text 重布局抖动会误关跟随，探针实测卡死）')

    def test_scroll_aid_bounded_timer(self):
        self.assertIn('id: scrollAid', self.msglist_block,
                      '必须有 scrollAid 兜底 Timer（大文本重布局跨多帧，'
                      '单次 callLater 会停在过期的 contentHeight 上）')
        self.assertIn('onContentHeightChanged', self.msglist_block,
                      'contentHeight 每次变化都要重滚（事件驱动跟随）')

    def test_handlers_call_scrolltoend(self):
        # 流式追加 / Markdown 替换 / 会话加载都要触发滚动
        self.assertRegex(self.conn_block,
                         r'onAppendToLast[\s\S]{0,400}?msgList\.scrollToEnd',
                         'onAppendToLast 必须调用 scrollToEnd（流式跟随）')
        self.assertRegex(self.conn_block,
                         r'onMessageReplaced[\s\S]{0,400}?msgList\.scrollToEnd',
                         'onMessageReplaced 必须调用 scrollToEnd（替换后回底）')
        self.assertRegex(self.conn_block,
                         r'onSessionLoaded[\s\S]{0,700}?msgList\.scrollToEnd\(true\)',
                         'onSessionLoaded 必须强制回底')
        self.assertRegex(self.conn_block,
                         r'onMessageAdded[\s\S]{0,400}?msgList\.scrollToEnd',
                         'onMessageAdded 必须调用 scrollToEnd（替代同步'
                         ' positionViewAtEnd —— 那在内容高度更新前执行会滚不到位）')
        self.assertNotIn('msgList.positionViewAtEnd()\n        }',
                         self.conn_block,
                         'onMessageAdded 里不应保留同步 positionViewAtEnd 直调')


if __name__ == '__main__':
    unittest.main()
