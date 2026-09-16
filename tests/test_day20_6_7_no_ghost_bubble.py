# -*- coding: utf-8 -*-
"""Day 20.6.7 护栏：修复「每次对话后有一个空回复」

根因：start_real_chat 预发「空 ai 占位气泡」并置 _last_who="ai"；思考模型
（QwQ 等）第一批流式 chunk 是 thinking，切段逻辑看到 who != _last_who 就
另建 thinking 气泡，正文再建第三个 —— 预占位的空 ai 气泡永远没人填。

修复：删除预占位，首个 chunk 由切段逻辑建气泡（_last_who 初始 None）。

护栏：
1. 思考模型序列（thinking→ai）只建两个气泡，无空 ai 残留
2. 非思考模型（纯 ai chunk）只建一个气泡
3. 思考→回答切段各自只建一次（连续同段 chunk 不重复建）
4. 完成后 finalize/replace 指向最后一段（ai），thinking 段不产生空气泡
"""
import sys
import unittest

from PyQt5.QtCore import QCoreApplication

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from qwen_app.chat_bridge import ChatBridge


class TestNoGhostBubble(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def _bridge_with_capture(self):
        b = ChatBridge(theme="light")
        added = []
        b.messageAdded.connect(lambda who, m: added.append((who, m.get("text", ""))))
        return b, added

    def test_thinking_model_no_ghost_bubble(self):
        """思考模型 chunk 序列：thinking→ai 只建两个气泡，无空 ai 占位残留"""
        b, added = self._bridge_with_capture()
        b._on_worker_chunk("思考A", True)
        b._on_worker_chunk("思考B", True)
        b._on_worker_chunk("正文A", False)
        b._on_worker_chunk("正文B", False)
        # 关键断言：恰好两个气泡（旧代码是三个：预占位空 ai + thinking + ai）
        self.assertEqual([w for w, _ in added], ["thinking", "ai"])
        # 且最后一个是 ai（正文气泡），不存在"空 ai 占位留在序列里"的情况
        self.assertEqual(added[-1][0], "ai")

    def test_plain_model_single_bubble(self):
        """非思考模型：首个 ai chunk 建唯一气泡"""
        b, added = self._bridge_with_capture()
        b._on_worker_chunk("直接回答", False)
        self.assertEqual([w for w, _ in added], ["ai"])

    def test_no_bubble_before_first_chunk(self):
        """发送后、首个 chunk 前不建任何气泡（无预占位）"""
        b, added = self._bridge_with_capture()
        # 模拟 start_real_chat 里 worker 启动后、chunk 到来前的窗口期：
        # 直接断言 _last_who 为 None（不再被预置为 "ai"）
        self.assertIsNone(b._last_who)
        self.assertEqual(added, [])

    def test_complete_finalizes_last_segment_only(self):
        """完成时 finalize/replace 指向最后一段 ai，不产生额外气泡"""
        b, added = self._bridge_with_capture()
        finalized = []
        replaced = []
        b.finalizeLast.connect(lambda who: finalized.append(who))
        b.messageReplaced.connect(lambda who, text: replaced.append(who))
        b._on_worker_chunk("思考", True)
        b._on_worker_chunk("回答", False)
        b._on_worker_complete("")
        self.assertEqual(finalized, ["ai"])
        self.assertEqual(replaced, ["ai"])
        # 全程 messageAdded 只有两个气泡（thinking + ai），没有空 ai
        self.assertEqual([w for w, _ in added], ["thinking", "ai"])
        self.assertIsNone(b._last_who)

    def test_error_before_any_chunk_no_bubble(self):
        """未收到任何 chunk 就出错：无气泡、无 finalize（不再留空气泡）"""
        b, added = self._bridge_with_capture()
        finalized = []
        b.finalizeLast.connect(lambda who: finalized.append(who))
        b._on_worker_error("boom")
        self.assertEqual(added, [])
        self.assertEqual(finalized, [])
        self.assertIsNone(b._last_who)


if __name__ == "__main__":
    unittest.main()
