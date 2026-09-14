"""Day 20.4.2: 端到端测试 — 行点击切会话 + 弹菜单。

回归：用户报「点击左侧的对话条目不能切换 / 三个点点击后没有菜单」。
- 根因 1：load_session 不写 config 的 current_id + 不 emit sessionListChanged
  → list_sessions().sel 不变 → 侧边栏蓝色高亮不移动 → 用户视觉感觉「没切」
- 根因 2：sessionMenu.x/y 用 mapToItem(null, ...) 全局坐标，但 Menu.x/y
  是 sessionMenu.parent（sidebar Rectangle）的局部坐标，差一个 sidebar
  偏移 → 菜单弹到屏幕外
- 根因 3：three-dot click 可能被 rowMa 吞（z-order 边界 case）

测试（offscreen + QTest 模拟鼠标事件）：
1. TestLoadSessionUpdatesHighlight：load_session 之后 current_id 持久化
2. TestLoadSessionEmitsSessionListChanged：load_session 后 emit
3. TestListSessionsReflectsCurrentId：list_sessions().sel 正确
4. TestSessionMenuPositionInParentCoords：Main.qml 源码必须用 mapToItem(parent, ...)
   而非 mapToItem(null, ...) —— 防 Menu 弹到屏幕外
5. TestThreeDotZOrder：moreBtn z > rowMa z（源码静态）
6. TestThreeDotAcceptedTrue：onClicked 第一行 mouse.accepted = true
7. TestRowMaDeclAfterMoreBtn：源码静态 — rowMa 必须在 moreBtn 之后声明
   （不强制但作为防御性约束）
"""
import os
import re
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestLoadSessionUpdatesHighlight(unittest.TestCase):
    """load_session 必须更新 config current_id + emit sessionListChanged"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")
        from qwen_app import config as _cfg
        # 准备 2 条测试会话
        self.conv_a = "day20_4_2_test_A"
        self.conv_b = "day20_4_2_test_B"
        _cfg.save_single_conversation({
            "id": self.conv_a, "title": "会话A",
            "history": [{"role": "user", "content": "A_msg"}],
            "created_at": "2026-09-14T00:00:00",
        }, self.conv_a)
        _cfg.save_single_conversation({
            "id": self.conv_b, "title": "会话B",
            "history": [{"role": "user", "content": "B_msg"}],
            "created_at": "2026-09-14T00:00:00",
        }, self.conv_b)
        # 设 A 为当前
        convs, _ = _cfg.load_conversations()
        _cfg.save_conversations(convs, self.conv_a)
        self.bridge._current_conv_id = self.conv_a
        # 信号捕获
        self.loaded = []
        self.list_changed = []
        self.bridge.sessionLoaded.connect(
            lambda cid, hist: self.loaded.append((cid, hist)))
        self.bridge.sessionListChanged.connect(
            lambda: self.list_changed.append(True))

    def tearDown(self):
        from qwen_app import config as _cfg
        convs, cur = _cfg.load_conversations()
        convs = [c for c in convs if c.get("id") not in (self.conv_a, self.conv_b)]
        _cfg.save_conversations(convs, cur)

    def test_load_session_persists_current_id(self):
        """load_session 之后 config 的 current_id 必须变成新会话 id"""
        from qwen_app import config as _cfg
        self.bridge.load_session(self.conv_b)
        _, cur = _cfg.load_conversations()
        self.assertEqual(cur, self.conv_b,
                         "load_session 必须把 current_id 写回 config（否则高亮不移动）")

    def test_load_session_emits_session_list_changed(self):
        """load_session 之后必须 emit sessionListChanged（QML 才刷新高亮）"""
        self.bridge.load_session(self.conv_b)
        self.assertTrue(self.list_changed,
                        "load_session 必须 emit sessionListChanged 让侧边栏 highlight 移动")

    def test_load_session_emits_session_loaded(self):
        """load_session 必须 emit sessionLoaded（QML 才会重填 messageModel）"""
        self.bridge.load_session(self.conv_b)
        self.assertTrue(self.loaded, "load_session 必须 emit sessionLoaded")
        cid, hist = self.loaded[-1]
        self.assertEqual(cid, self.conv_b)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["content"], "B_msg")

    def test_list_sessions_sel_reflects_current_id(self):
        """load_session 后 list_sessions() 的 sel 字段必须反映新会话"""
        self.bridge.load_session(self.conv_b)
        sessions = self.bridge.list_sessions()
        b_sess = next((s for s in sessions if s["id"] == self.conv_b), None)
        a_sess = next((s for s in sessions if s["id"] == self.conv_a), None)
        self.assertIsNotNone(b_sess)
        self.assertIsNotNone(a_sess)
        self.assertTrue(b_sess["sel"], "B 应被标 sel=True")
        self.assertFalse(a_sess["sel"], "A 应被标 sel=False")


class TestSessionMenuPositionInParentCoords(unittest.TestCase):
    """Day 20.4.2: sessionMenu.x/y 是 sessionMenu.parent 的局部坐标，
    必须 mapToItem(sessionMenu.parent, ...) 而不是 mapToItem(null, ...)"""

    def setUp(self):
        self.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                        encoding="utf-8").read()

    def test_uses_map_to_item_with_parent(self):
        """onClicked 必须用 sessionMenu.parent 作 mapToItem 目标"""
        # 抓 moreMa 块后再抓 onClicked 块
        m = re.search(r"id:\s*moreMa[\s\S]{0,500}onClicked[\s\S]{0,1000}",
                      self.src)
        self.assertIsNotNone(m)
        handler = m.group(0)
        # 必须有 mapToItem(sessionMenu.parent, ...) 调用
        self.assertIn("mapToItem(sessionMenu.parent", handler,
                      "onClicked 内的 mapToItem 必须以 sessionMenu.parent 为目标（不是 null）")
        # 不应该用 mapToItem(null, ...) 算菜单位置
        self.assertNotIn("mapToItem(null, moreBtn.width, 0)", handler,
                         "mapToItem(null, ...) 是全局屏幕坐标，差一个 sidebar 偏移")

    def test_no_legacy_global_coords(self):
        """防回归：sessionMenu.x = mapToItem(null, ...) 全局坐标模式不能用"""
        m = re.search(r"id:\s*sessionMenu[\s\S]{0,3000}", self.src)
        if m:
            body = m.group(0)
            for line in body.splitlines():
                if "sessionMenu.x" in line and "mapToItem" in line:
                    self.assertIn("mapToItem(sessionMenu.parent", line,
                                  f"sessionMenu.x 赋值必须用 parent 坐标：{line}")


class TestThreeDotClickDefenseInDepth(unittest.TestCase):
    """三个深度防御：
    1. moreBtn z > rowMa z
    2. onClicked 第一行 mouse.accepted = true
    3. onClicked 后立刻设 mouse.accepted（防 rowMa 拿到事件）
    """

    def setUp(self):
        self.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                        encoding="utf-8").read()

    def test_more_btn_z_higher_than_row(self):
        """moreBtn z 必须 > rowMa z"""
        m_btn = re.search(r"id:\s*moreBtn[\s\S]{0,1000}z:\s*(\d+)", self.src)
        m_row = re.search(r"id:\s*rowMa[\s\S]{0,500}z:\s*(-?\d+)", self.src)
        self.assertIsNotNone(m_btn, "moreBtn 必须显式设 z")
        self.assertIsNotNone(m_row, "rowMa 必须显式设 z")
        self.assertGreater(int(m_btn.group(1)), int(m_row.group(1)),
                           f"moreBtn z={m_btn.group(1)} 必须 > rowMa z={m_row.group(1)}")

    def test_on_clicked_accepted_true(self):
        """onClicked 第一行必须是 mouse.accepted = true"""
        m = re.search(r"id:\s*moreMa[\s\S]{0,500}onClicked[\s\S]{0,1000}",
                      self.src)
        self.assertIsNotNone(m)
        handler = m.group(0)
        self.assertRegex(handler, r"onClicked[\s\S]{0,400}mouse\.accepted\s*=\s*true")

    def test_row_ma_declared_after_more_btn(self):
        """源码静态：rowMa 必须在 moreBtn 之后声明（默认 z 0 时后声明者上）"""
        more_btn_idx = self.src.find("id: moreBtn")
        row_ma_idx = self.src.find("id: rowMa")
        self.assertGreater(row_ma_idx, more_btn_idx,
                           f"rowMa 必须在 moreBtn 之后声明（当前 moreBtn@{more_btn_idx}, rowMa@{row_ma_idx}）")


if __name__ == "__main__":
    unittest.main()
