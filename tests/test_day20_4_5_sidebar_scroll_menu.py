# -*- coding: utf-8 -*-
"""Day 20.4.5 回归测试 —— 两个新报的 bug 的静态 + 行为护栏。

用户报（点非当前会话之后）：
  1. 「左侧会自动跳回对话列表最顶」
  2. 「三个点的菜单出现位置有问题」

实测结论：
  BUG2 已复现并定位：菜单位置是用 `index * convListView.rowStride - contentY`
  反推的。**深层滚动后 ListView delegate 的 `index` 不可靠** —— 实测同一个
  delegate 的 y=11036（=178 行）却报 index=3，`ListView.indexAt()` 也返回 3，
  于是 rowTop=-10534（大负数），`sessionMenu.y` 被钳到 0 → 菜单跑到窗口顶部
  （也就是"对话列表最顶"）。而 `moreBtn.mapToItem(Overlay.overlay, ...)` 在同一
  时刻给出的坐标是正确的。→ 改为从按钮自身取坐标，并删掉 rowStride 反推。

  BUG1（列表跳顶）在本机**未能复现**（用 positionViewAtIndex / 真实滚轮 / flick
  手势 + 真点击 五种方式都没让 contentY 归零），但顺手清掉了两个会导致列表
  "自己动"的真实隐患：
    a) `load_session` 原来调 `save_conversations(convs, conv_id)` 全量重写 185 行；
       而 `load_conversations()` 返回的 dict **不含 updated_at**，save 里
       `conv.get("updated_at", conv.get("created_at",""))` 会回退 → **把排序键
       updated_at 抹平成 created_at**，侧边栏正是 `ORDER BY updated_at DESC`
       → 点一下会话整个列表可能重排。改为只更新 current_id 的
       `set_current_conversation()`。
    b) QML `refreshConvList()` 原来无条件 `convModel.clear()` + 185 次 append，
       等于每次点会话都给 ListView 一次 model reset。改为：会话集合没变时
       **只就地更新 sel/name/time**（不 reset）；集合变了才重建并恢复 contentY。
"""
import os
import re
import sqlite3
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("//"))


class TestMenuPositionedFromButtonNotIndex(unittest.TestCase):
    """BUG2：菜单必须从按钮自身 mapToItem 取坐标，不能再按 index 反推。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _strip_comments(_read(MAIN_QML))

    def test_no_index_rowstride_math(self):
        """不得再用 `index * ... rowStride - contentY` 反推行位置。"""
        self.assertNotIn("rowStride", self.src,
                         "rowStride 反推方案已废弃（深层滚动时 index 不可靠），应删除")
        self.assertNotRegex(
            self.src, r"index\s*\*\s*\d+\s*-\s*\w+\.contentY",
            "不得用 index*行高-contentY 反推行的视口位置")

    def test_positions_from_morebtn_map_to_overlay(self):
        """必须用 moreBtn.mapToItem(Overlay 基准, ...) 取按钮坐标。"""
        i = self.src.index("id: moreMa")
        seg = self.src[i:i + 2500]
        self.assertIn("moreBtn.mapToItem(ov", seg,
                      "菜单定位必须从 moreBtn 自身映射到 Overlay 坐标")
        self.assertIn("Overlay.overlay", seg)

    def test_popup_before_xy(self):
        """popup() 会在内部重排一次覆盖预设 x/y，必须先 popup() 再设坐标。"""
        i = self.src.index("id: moreMa")
        seg = self.src[i:i + 2500]
        self.assertLess(seg.index("sessionMenu.popup()"), seg.index("sessionMenu.x ="))

    def test_xy_clamped_into_window(self):
        """x/y 都要夹到窗口内，避免负值（实测负数会被钳到 0 → 菜单贴顶）。"""
        i = self.src.index("id: moreMa")
        seg = self.src[i:i + 2500]
        self.assertRegex(seg, r"var mx = Math\.max\(4, Math\.min\(")
        self.assertRegex(seg, r"var my = Math\.max\(4, Math\.min\(")


class TestConvListNoResetOnSelectionChange(unittest.TestCase):
    """BUG1 加固：选中态变化不应触发 model reset。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(MAIN_QML)
        m = re.search(r"function refreshConvList\(\)\s*\{([\s\S]*?)\n    \}",
                      cls.src)
        assert m, "找不到 refreshConvList"
        cls.body = _strip_comments(m.group(1))

    def test_has_in_place_update_branch(self):
        """必须存在"集合没变 → 只 set() 不 clear()"的就地更新分支。"""
        self.assertIn("sameShape", self.body,
                      "refreshConvList 必须先判断会话集合是否没变")
        self.assertIn("convModel.set(", self.body,
                      "集合没变时必须用 convModel.set() 就地更新（不 reset）")

    def test_clear_only_happens_after_shape_check(self):
        """clear() 只能出现在 sameShape 判断**之后**（重建分支里）。"""
        self.assertIn("convModel.clear()", self.body)
        self.assertLess(self.body.index("sameShape"), self.body.index("convModel.clear()"),
                        "必须先把 sameShape 判断写在 clear() 之前")

    def test_restores_content_y_after_rebuild(self):
        """重建分支必须恢复/夹取 contentY，避免列表跳回最顶。"""
        self.assertIn("keepY", self.body, "重建前必须记下 contentY")
        seg = self.body[self.body.index("convModel.clear()"):]
        self.assertRegex(seg, r"convListView\.contentY\s*=",
                         "重建后必须把 contentY 恢复回去")
        self.assertRegex(seg, r"Math\.min\(keepY",
                         "恢复 contentY 时要夹到合法范围（Math.min(keepY, maxY)）")


class TestLoadSessionDoesNotRewriteTable(unittest.TestCase):
    """BUG1 加固（行为）：load_session 只更新 current_id，不重写会话表。

    否则会把排序键 updated_at 抹平成 created_at（侧边栏 ORDER BY updated_at
    DESC → 列表重排），而且每次点会话重写 185 行纯属浪费。
    """

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app import config as _cfg
        self._cfg = _cfg
        self.bridge = ChatBridge(theme="light")
        self.a = "day20_4_5_A"
        self.b = "day20_4_5_B"
        # 记下原始 current_id，测完还原（别把用户当前选中的会话改掉）
        try:
            _, self._orig_cur = _cfg.load_conversations()
        except Exception:
            self._orig_cur = None
        # A/B 的 updated_at 刻意与 created_at 不同，用来检测是否被抹平。
        # save_single_conversation 的第 2 参就是 current_id，所以存 B 时把
        # current_id 指到 B —— 后面 load_session(A) 才是「切到非当前会话」。
        # ⚠ 绝不能再用 load_conversations()+save_conversations() 去设 current_id：
        # 那个 load→save 往返**本身**就会把 updated_at 抹成 created_at
        # （load_conversations 的 SELECT 不含 updated_at），会先污染掉本用例
        # 要断言的数据 —— 之前就是踩了这个坑导致用例自己把自己判失败。
        _cfg.save_single_conversation({
            "id": self.a, "title": "A", "history": [],
            "created_at": "2020-01-01T00:00:00",
            "updated_at": "2031-12-31T23:59:59",
        }, self.a)
        _cfg.save_single_conversation({
            "id": self.b, "title": "B", "history": [{"role": "user", "content": "hi"}],
            "created_at": "2020-01-02T00:00:00",
            "updated_at": "2031-12-30T23:59:59",
        }, self.b)
        self.loaded, self.changed = [], []
        self.bridge.sessionLoaded.connect(lambda cid, h: self.loaded.append(cid))
        self.bridge.sessionListChanged.connect(lambda: self.changed.append(True))

    def tearDown(self):
        # 直接删掉本用例造的 A/B 两行；**不要**走 load→save 往返，
        # 那会顺带把真实库里的其它行也重写一遍（污染 updated_at）。
        db = sqlite3.connect(self._cfg.CONVERSATIONS_DB)
        try:
            db.execute("DELETE FROM conversations WHERE id IN (?,?)", (self.a, self.b))
            db.commit()
        finally:
            db.close()
        if self._orig_cur:
            try:
                self._cfg.set_current_conversation(self._orig_cur)
            except Exception:
                pass

    def _updated_at(self, cid):
        db = sqlite3.connect(self._cfg.CONVERSATIONS_DB)
        try:
            r = db.execute("SELECT updated_at FROM conversations WHERE id=?",
                           (cid,)).fetchone()
            return r[0] if r else None
        finally:
            db.close()

    def test_updated_at_not_clobbered_by_load_session(self):
        self.bridge.load_session(self.a)
        self.assertEqual(self._updated_at(self.a), "2031-12-31T23:59:59",
                         "load_session 不得改写 updated_at（会被抹成 created_at）")
        self.assertEqual(self._updated_at(self.b), "2031-12-30T23:59:59",
                         "load_session 不得碰到其它会话行")

    def test_current_id_persisted_and_signals_emitted(self):
        self.bridge.load_session(self.a)
        _, cur = self._cfg.load_conversations()
        self.assertEqual(cur, self.a, "load_session 必须持久化 current_id（高亮要跟随）")
        self.assertEqual(self.loaded, [self.a], "必须 emit sessionLoaded")
        self.assertTrue(self.changed, "必须 emit sessionListChanged")


if __name__ == "__main__":
    unittest.main()
