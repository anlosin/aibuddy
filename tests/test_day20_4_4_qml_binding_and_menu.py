# -*- coding: utf-8 -*-
"""Day 20.4.4 回归测试 —— 三个真 bug 的静态护栏。

用户连报三次的三个问题（前两次都没真正修好）：
  1. 点击左侧会话条目「不能切换」
  2. 每个会话的「⋯」点击后没有菜单
  3. 对话气泡里的内容完全看不见

真因（用真渲染 + 真点击 + 读活对象属性实测出来的）：
  A. `MessageBubble { who: who; text: text }` —— QML 作用域遮蔽：
     对象自身的 who/text/hasCode 属性优先于 delegate 的 model role，
     RHS 解析成 MessageBubble 自己 → 自绑定 → 永远停在默认值
     who="ai" / text=""。表现：所有气泡都是 AI 白气泡、正文一个字都没有。
     修复：显式写 model.who / model.text / model.hasCode / model.code。
  B. 侧边栏 rowMa（覆盖整个 delegate）声明在 RowLayout 之后 → 命中测试
     按绘制顺序逆序 → rowMa 永远先吃掉 click，moreBtn 永远点不到。
     （moreBtn 上的 z:10 只在 RowLayout 内部生效，对 rowMa 无效。）
     修复：rowMa 压到 RowLayout 之下（z: -1）。
  C. moreBtn `visible: rowMa.containsMouse || model.sel` 是抖动陷阱：
     鼠标移到 ⋯ 上时 hover 从 rowMa 切给 moreMa → rowMa.containsMouse 变 false
     → 按钮消失 → 鼠标下没东西 → hover 回到 rowMa → 按钮又出现……点不中。
     修复：常显（visible: true）。
  D. Popup 定位：x/y 是「相对 Popup.parent」的坐标，而 Qt 只有在
     parent == Overlay 时才尊重我们设的 x/y；并且 popup() 内部会按自己的
     规则重排一次、把事先设好的 x/y 覆盖掉（实测设 46,62 → 实际 888,448）。
     修复：parent: Overlay.overlay + 「先 popup() 再设 x/y」。
"""

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_QML = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")
BUBBLE_QML = os.path.join(_ROOT, "qwen_app", "qml", "MessageBubble.qml")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    """去掉 // 行注释，避免注释里的示例代码干扰断言。"""
    out = []
    for line in src.splitlines():
        s = line.lstrip()
        if s.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


class TestMessageBubbleModelRoleBinding(unittest.TestCase):
    """Bug A：MessageBubble 实例化必须显式写 model.xxx，否则属性遮蔽成自绑定。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(MAIN_QML)
        m = re.search(r"MessageBubble\s*\{([^}]*)\}", cls.src, re.S)
        assert m, "Main.qml 里找不到 MessageBubble 实例化"
        cls.block = m.group(1)

    def test_uses_model_prefix_for_shadowed_props(self):
        """who / text / hasCode / code 都会被 MessageBubble 自身属性遮蔽，
        必须走 model. 前缀。"""
        for prop in ("who", "text", "hasCode", "code"):
            self.assertRegex(
                self.block, r"model\.%s\b" % prop,
                f"MessageBubble 的 {prop} 必须写成 model.{prop}"
                f"（否则 RHS 解析到 MessageBubble 自己 → 自绑定 → 默认值）")

    def test_no_unqualified_shadowing_binding(self):
        """防回归：不能再出现 who: who / text: text / hasCode: hasCode。"""
        code = _strip_comments(self.block)
        for prop in ("who", "text", "hasCode"):
            self.assertNotRegex(
                code, r"(?<!model\.)(?<!\.)\b%s\s*:\s*%s\b" % (prop, prop),
                f"检测到 `{prop}: {prop}` —— 这是自绑定，气泡会变成空白")


class TestSidebarThreeDotReachable(unittest.TestCase):
    """Bug B/C：三点按钮必须能收到 click，且一直可见。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(MAIN_QML)

    def test_row_ma_below_row_layout(self):
        """rowMa 必须在绘制顺序上低于 RowLayout —— 否则吃掉整个 delegate 的 click。"""
        m = re.search(r"id:\s*rowMa\b[^}]*?\bz:\s*(-?\d+)", self.src, re.S)
        self.assertIsNotNone(m, "rowMa 必须显式设置 z")
        self.assertLess(int(m.group(1)), 0,
                        "rowMa 的 z 必须 < 0（RowLayout 默认 0），否则三点按钮点不到")

    def test_more_btn_always_visible(self):
        """三点按钮不能依赖 hover 才可见（会形成 hover 抖动，永远点不中）。"""
        seg = self.src[self.src.index("id: moreBtn"):]
        seg = seg[:seg.index("MouseArea")] if "MouseArea" in seg else seg[:600]
        code = _strip_comments(seg)
        self.assertRegex(code, r"visible:\s*true\b",
                         "侧边栏三点按钮必须常显（visible: true）")
        self.assertNotIn("containsMouse",
                         code.split("visible:")[1].split("\n")[0],
                         "visible 不能绑定到 containsMouse —— hover 会抖动")


class TestPopupPositioning(unittest.TestCase):
    """Bug D：Popup 必须 parent=Overlay，且「先 popup() 再设 x/y」。"""

    def test_main_session_menu_parent_is_overlay(self):
        src = _read(MAIN_QML)
        seg = src[src.index("id: sessionMenu"):]
        seg = seg[:800]
        self.assertRegex(_strip_comments(seg), r"parent:\s*Overlay\.overlay",
                         "sessionMenu.parent 必须是 Overlay.overlay")

    def test_main_popup_before_xy(self):
        src = _read(MAIN_QML)
        code = _strip_comments(src)
        i = code.index("id: moreMa")
        seg = code[i:i + 4000]
        self.assertIn("sessionMenu.popup()", seg)
        p = seg.index("sessionMenu.popup()")
        x = seg.index("sessionMenu.x")
        self.assertLess(p, x,
                        "必须先 sessionMenu.popup() 再设 x/y —— popup() 内部会覆盖"
                        "事先设好的 x/y（实测设 46,62 → 实际 888,448）")

    def test_bubble_menu_parent_is_overlay(self):
        src = _read(BUBBLE_QML)
        seg = src[src.index("id: bubbleMenu"):]
        self.assertRegex(_strip_comments(seg[:600]), r"parent:\s*Overlay\.overlay",
                         "bubbleMenu.parent 必须是 Overlay.overlay")

    def test_bubble_popup_before_xy(self):
        src = _strip_comments(_read(BUBBLE_QML))
        i = src.index("id: moreMa")
        seg = src[i:i + 3000]
        self.assertIn("bubbleMenu.popup()", seg)
        p = seg.index("bubbleMenu.popup()")
        x = seg.index("bubbleMenu.x")
        self.assertLess(p, x, "必须先 bubbleMenu.popup() 再设 x/y")


class TestRowStrideRemoved(unittest.TestCase):
    """Day 20.4.5：rowStride 反推方案已废弃。

    深层滚动时 delegate 的 index 不可靠（实测 delegate y=11036 ≈ 第 178 行，
    却报 index=3；ListView.indexAt() 同样返回 3）。用它算 rowTop =
    179 + 3*62 - 10899 = -10534 → my = -10472 → Qt 把 Menu.y 夹到 0 →
    菜单跑到屏幕顶部。菜单位置改为从 moreBtn 自身 mapToItem(Overlay.overlay)
    取坐标，因此 rowStride 不该再存在（留着就会诱导后人继续用）。
    """

    def test_row_stride_gone(self):
        src = _strip_comments(_read(MAIN_QML))
        self.assertNotIn("rowStride", src,
                         "rowStride 已废弃（菜单改为从按钮自身取坐标，"
                         "深层滚动时 delegate index 不可靠）")


class TestBubbleImplicitWidthDecoupled(unittest.TestCase):
    """Bug E：MessageBubble.implicitWidth 不能由 contentRow.implicitWidth 反推。

    标题 Text 用 Layout.fillWidth + wrapMode → 其 implicitWidth 依赖
    Text.width ← contentRow.width ← bubble.width ← bubble.implicitWidth，
    于是 implicitWidth 自环；Qt 会就近归到 implicitHeight 上报
    "Binding loop detected for property implicitHeight"，每建一个气泡就往
    stderr 打一条（用户容易误当报错）。实测：implicitWidth 解耦成常量后消失。
    """

    def test_implicit_width_not_derived_from_content_row(self):
        src = _strip_comments(_read(BUBBLE_QML))
        m = re.search(r"\bimplicitWidth\s*:\s*([^\n]+)", src)
        self.assertIsNotNone(m, "MessageBubble 必须有 implicitWidth")
        rhs = m.group(1).strip()
        self.assertNotIn("contentRow", rhs,
                         f"implicitWidth 不能引用 contentRow（会形成 implicitWidth "
                         f"自环 → Qt 报 implicitHeight binding loop）：{rhs!r}")

    def test_implicit_width_is_const_or_property(self):
        """implicitWidth 的 RHS 必须是常量或简单属性（无 . 链），保证无环。"""
        src = _strip_comments(_read(BUBBLE_QML))
        m = re.search(r"\bimplicitWidth\s*:\s*([^\n]+)", src)
        self.assertIsNotNone(m, "MessageBubble 必须有 implicitWidth")
        rhs = m.group(1).strip().rstrip(";")
        self.assertRegex(rhs, r"^(preferredWidth|\d+)$",
                         f"implicitWidth 应为常量或 preferredWidth 属性，当前：{rhs!r}")


if __name__ == "__main__":
    unittest.main()
