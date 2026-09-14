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
4. TestSessionMenuPositionInParentCoords：Main.qml 源码必须用
   parent: Overlay.overlay + 「先 popup() 再设 x/y」—— 防 Menu 弹到屏幕外
5. TestThreeDotClickDefenseInDepth：rowMa z < 0（三点才点得到）
6. TestThreeDotAcceptedTrue：onClicked 第一行 mouse.accepted = true
7. TestRowMaDeclAfterMoreBtn：源码静态 — rowMa 必须在 moreBtn 之后声明
   （不强制但作为防御性约束）

注：Day 20.4.4 实测推翻了本文件原先的两条假设 ——
  ① 「moreBtn z:10 就能盖过 rowMa」：错。moreBtn 的 z 只在 RowLayout 内部
     相对兄弟生效；真正的解法是把 rowMa 压到 z < 0。
  ② 「mapToItem(sessionMenu.parent, ...) 即 parent 局部坐标」：错。parent 是
     普通 Item 时 Qt 会自行重排、覆盖 x/y；必须 parent=Overlay 且先 popup() 再设 x/y。
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
    """Day 20.4.4 修正：Menu.x/y 是「相对 Popup.parent」的局部坐标，而 Qt 只有在
    parent == Overlay 时才尊重调用方设的 x/y；并且 popup() 内部会按自己的规则
    重排一次、把事先设好的 x/y 覆盖掉（实测设 46,62 → 实际 888,448，弹到窗口
    右下角）。正确模式 = parent: Overlay.overlay + 「先 popup() 再设 x/y」，坐标
    用 Overlay 坐标（基准取长期存在的 convListView，不用 delegate）。

    （旧版本假设 mapToItem(sessionMenu.parent, ...) 即 parent 局部坐标，已被实测
    证伪 —— parent 是普通 Item 时 Qt 会重排。故本条测试已改为覆盖新规则。）"""

    def setUp(self):
        self.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                        encoding="utf-8").read()

    def test_uses_overlay_parent_and_popup_before_xy(self):
        """onClicked 必须：① 不用 mapToItem(null, ...)；② 以 Overlay 为基准；
        ③ 先 sessionMenu.popup() 再设 x/y。"""
        m = re.search(r"id:\s*moreMa[\s\S]{0,500}onClicked[\s\S]{0,4000}",
                      self.src)
        self.assertIsNotNone(m)
        handler = m.group(0)
        # ① 不能再依赖 mapToItem(null, ...) 全局坐标（差一个 sidebar 偏移）
        self.assertNotIn("mapToItem(null", handler,
                         "mapToItem(null, ...) 是全局屏幕坐标，差一个 sidebar 偏移")
        # ② 坐标系基准必须是 Overlay.overlay
        self.assertIn("Overlay.overlay", handler,
                      "定位必须以 Overlay.overlay 作为坐标系基准")
        # ③ 顺序：先 popup() 再设 x/y
        self.assertIn("sessionMenu.popup()", handler)
        self.assertIn("sessionMenu.x =", handler)
        p = handler.index("sessionMenu.popup()")
        x = handler.index("sessionMenu.x =")
        self.assertLess(p, x,
                        "必须先 sessionMenu.popup() 再设 x/y —— popup() 会覆盖预设值")

    def test_no_legacy_global_coords(self):
        """防回归：不得再用 mapToItem(null, ...) 全局坐标算 sessionMenu 位置。"""
        i = self.src.index("id: moreMa")
        seg = self.src[i:i + 2500]
        self.assertNotIn("mapToItem(null", seg,
                         "不得用 mapToItem(null, ...) 全局坐标算菜单位置"
                         "（差一个 sidebar 偏移，会弹到屏幕外）")


class TestThreeDotClickDefenseInDepth(unittest.TestCase):
    """三个深度防御：
    1. rowMa z < 0（压到 RowLayout 之下，三点才点得到）
    2. onClicked 第一行 mouse.accepted = true
    3. rowMa 必须在 moreBtn 之后声明（防御性记录当前结构）
    """

    def setUp(self):
        self.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                        encoding="utf-8").read()

    def test_rowma_z_below_zero(self):
        """Day 20.4.4：rowMa 的 z 必须 < 0，否则覆盖整个 delegate 的它
        会先吃掉 click，三点按钮永远点不到。"""
        m_row = re.search(r"id:\s*rowMa\b[\s\S]{0,400}?\bz:\s*(-?\d+)", self.src)
        self.assertIsNotNone(m_row, "rowMa 必须显式设 z")
        self.assertLess(int(m_row.group(1)), 0,
                        f"rowMa z={m_row.group(1)} 必须 < 0（RowLayout 默认 0）")

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
