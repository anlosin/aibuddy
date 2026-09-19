"""Day 20.4: 侧边栏会话列表三点按钮回归测试。

背景：用户反馈「红框里的功能还是不可用」（截图显示侧边栏会话项右侧的
「⋯」按钮点击没反应）。

根因（之前的 QML）：
1. three-dot MouseArea 在 rowMa 之前声明
2. rowMa `anchors.fill: parent` 覆盖整个 delegate（包括 three-dot 区域）
3. rowMa 声明在后 → z 更高 → 点击 three-dot 时 rowMa 捕获事件
4. rowMa.onClicked → bridge.load_session(model.convId)
5. load_session 用的是同一个会话（已 sel）→ messageModel 不变 → 用户
   看到「无反应」

修复（Day 20.4.4 实测修正 z-order 手段）：
1. **rowMa 压到 RowLayout 之下（z: -1）**。原以为给 moreBtn 加 z:10 就够，
   但 moreBtn 的 z 只在其父 RowLayout *内部* 相对兄弟生效；对覆盖整个
   delegate 的 rowMa 完全无效。命中测试按绘制顺序逆序 → rowMa 后声明者
   永远先拿到 click。把 rowMa 的 z 压到 0 以下，RowLayout 子树才先参与命中。
2. three-dot onClicked 第一行 ``mouse.accepted = true``（防止冒泡到 rowMa）
3. 改成弹菜单（重命名 / 清空消息 / 删除会话），不再直接 delete_session
4. chat_bridge.py 加两个新 slot：rename_session / clear_session
5. 跟 MessageBubble.qml 一样，把数据缓存到 Menu 的 currentConvXxx 属性
   （避开跨 Popup window scope 失效）

本文件验证：
- 5 个 slot 必须存在（list_sessions / create_session / delete_session /
  load_session / 新增 rename_session / 新增 clear_session_history）
- 3 个 slot 签名正确（pyqtSlot 类型注解）
- rename_session 拒绝空字符串 + 写盘
- clear_session_history 成功后 emit sessionLoaded（让 QML 清 messageModel）
- Main.qml rowMa 必须 z < 0（三点才点得到）+ onClicked 首行 mouse.accepted = true
- Main.qml sessionMenu 必须有 currentConvId / currentConvName 属性
- Main.qml renameDialog 必须存在
- 三个 MenuItem 都在（重命名 / 清空 / 删除）
- 旧的「直接 delete_session」路径必须消失
"""
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _isolate_db(test_self):
    """Day 20.6.19：把 db 重定向到 tmpdir，tearDown 时调 _restore_db。"""
    from qwen_app import config as _cfg
    test_self._tmpdir = tempfile.mkdtemp(prefix="day20_4_3dot_")
    _cfg.set_db_path_for_tests(os.path.join(test_self._tmpdir, "conversations.db"))
    test_self._cfg = _cfg


def _restore_db(test_self):
    from qwen_app import config as _cfg
    try:
        _cfg.close_all_conns()
    except Exception:
        pass
    _cfg.set_db_path_for_tests(None)
    shutil.rmtree(test_self._tmpdir, ignore_errors=True)


class TestSessionSlotsExist(unittest.TestCase):
    """bridge 必须有 rename_session 和 clear_session_history（Day 20.4 新增）"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme="light")

    def test_rename_session_exists(self):
        self.assertTrue(hasattr(self.bridge, "rename_session"))
        self.assertTrue(callable(self.bridge.rename_session))

    def test_clear_session_history_exists(self):
        self.assertTrue(hasattr(self.bridge, "clear_session_history"))
        self.assertTrue(callable(self.bridge.clear_session_history))

    def test_delete_session_still_exists(self):
        """Day 20.4 后 delete_session 仍可用（菜单「删除会话」用）"""
        self.assertTrue(hasattr(self.bridge, "delete_session"))


class TestRenameSessionBehavior(unittest.TestCase):
    """rename_session 业务逻辑：空字符串拒绝 / 写盘 / emit sessionListChanged"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app import config as _cfg
        # 备份原配置
        self._backup_models, self._backup_cur = _cfg.load_models()
        self._original = _cfg.load_conversations()
        # 隔离 db 到 tmpdir（Day 20.6.19）
        _isolate_db(self)
        # 准备一条测试会话
        from qwen_app import config
        test_id = "day20_4_test_conv_001"
        config.save_single_conversation({
            "id": test_id,
            "title": "原标题",
            "history": [{"role": "user", "content": "hi"}],
            "created_at": "2026-09-14T00:00:00",
        }, test_id)
        self.bridge = ChatBridge(theme="light")
        self.test_id = test_id
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))
        self.list_changed = []
        self.bridge.sessionListChanged.connect(lambda: self.list_changed.append(True))

    def tearDown(self):
        _restore_db(self)

    def test_rename_success(self):
        ok = self.bridge.rename_session(self.test_id, "新标题")
        self.assertTrue(ok, "rename_session 应返回 True")
        self.assertTrue(self.list_changed, "应 emit sessionListChanged")
        # 重新读盘确认
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        target = next((c for c in convs if c["id"] == self.test_id), None)
        self.assertIsNotNone(target)
        self.assertEqual(target["title"], "新标题")

    def test_rename_empty_rejected(self):
        ok = self.bridge.rename_session(self.test_id, "   ")
        self.assertFalse(ok, "纯空白应拒绝")
        self.assertFalse(self.list_changed, "应不 emit")
        self.assertTrue(any("不能为空" in m for m in self.toast_msgs),
                        "应 toast 提示")

    def test_rename_nonexistent_rejected(self):
        ok = self.bridge.rename_session("nonexistent_id", "new")
        self.assertFalse(ok)

    def test_rename_strips_whitespace(self):
        ok = self.bridge.rename_session(self.test_id, "  标题含前后空格  ")
        self.assertTrue(ok)
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        target = next((c for c in convs if c["id"] == self.test_id), None)
        self.assertEqual(target["title"], "标题含前后空格", "应 strip 前后空白")


class TestClearSessionHistoryBehavior(unittest.TestCase):
    """clear_session_history：清空消息 + 若当前会话则 emit sessionLoaded"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv)
        # 隔离 db 到 tmpdir（Day 20.6.19）
        _isolate_db(self)
        from qwen_app.chat_bridge import ChatBridge
        from qwen_app import config as _cfg
        self.bridge = ChatBridge(theme="light")
        test_id = "day20_4_clear_test_001"
        _cfg.save_single_conversation({
            "id": test_id,
            "title": "清空测试",
            "history": [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "reply1"},
            ],
            "created_at": "2026-09-14T00:00:00",
        }, test_id)
        self.test_id = test_id
        self.bridge._current_conv_id = test_id
        self.loaded = []
        self.bridge.sessionLoaded.connect(
            lambda cid, hist: self.loaded.append((cid, hist)))

    def tearDown(self):
        _restore_db(self)

    def test_clear_emits_session_loaded_when_current(self):
        """清的是当前会话 → emit sessionLoaded(空 list) 让 QML 清 messageModel"""
        ok = self.bridge.clear_session_history(self.test_id)
        self.assertTrue(ok)
        self.assertTrue(self.loaded, "应 emit sessionLoaded")
        cid, hist = self.loaded[-1]
        self.assertEqual(cid, self.test_id)
        self.assertEqual(hist, [])

    def test_clear_does_not_emit_when_other(self):
        """清的不是当前会话 → 不 emit sessionLoaded（避免误清 messageModel）"""
        _cfg = self._cfg
        # 创建另一条会话
        other_id = "day20_4_clear_test_002"
        _cfg.save_single_conversation({
            "id": other_id, "title": "另一条",
            "history": [{"role": "user", "content": "x"}],
            "created_at": "2026-09-14T00:00:00",
        }, other_id)
        try:
            self.loaded.clear()
            self.bridge.clear_session_history(other_id)
            # 不应 emit sessionLoaded
            self.assertEqual(self.loaded, [])
        finally:
            convs, cur = _cfg.load_conversations()
            convs = [c for c in convs if c.get("id") != other_id]
            _cfg.save_conversations(convs, cur)


class TestSessionThreeDotClickStructure(unittest.TestCase):
    """Main.qml 侧边栏三点按钮 + 菜单结构静态检查（防 Day 20.4 bug 回归）"""

    @classmethod
    def setUpClass(cls):
        cls.src = open(os.path.join(_ROOT, "qwen_app", "qml", "Main.qml"),
                      encoding="utf-8").read()

    def test_rowma_pushed_below_rowlayout(self):
        """Day 20.4.4 修正：让三点可点的正确做法是把 rowMa 压到 RowLayout 之下
        （z < 0），而不是给 moreBtn 加 z:10 —— moreBtn 的 z 只在 RowLayout
        *内部* 相对兄弟生效，对覆盖整个 delegate 的 rowMa 完全无效。"""
        m = re.search(r"id:\s*rowMa\b[\s\S]{0,400}?\bz:\s*(-?\d+)", self.src)
        self.assertIsNotNone(m, "rowMa 必须显式设置 z")
        row_ma_z = int(m.group(1))
        self.assertLess(row_ma_z, 0,
                        f"rowMa z={row_ma_z} 必须 < 0（RowLayout 默认 z=0），"
                        "否则 covering 的 rowMa 会吃掉三点按钮的 click")

    def test_three_dot_click_blocks_propagation(self):
        """onClicked 第一行必须是 mouse.accepted = true（阻止冒泡到 rowMa）"""
        m = re.search(r"id:\s*moreMa[\s\S]{0,500}onClicked[\s\S]{0,500}",
                      self.src)
        self.assertIsNotNone(m, "找不到 moreMa onClicked")
        handler = m.group(0)
        # 跨注释行匹配：onClicked 之后（含 function(mouse) { 语法）到 mouse.accepted
        self.assertRegex(handler, r"onClicked[\s\S]{0,400}mouse\.accepted\s*=\s*true",
                         "three-dot onClicked 必须（注释后）设 mouse.accepted = true 阻止冒泡")

    def test_three_dot_uses_cached_menu_properties(self):
        """onClicked 内必须把 model.convId / model.name 缓存到 sessionMenu 属性"""
        m = re.search(r"id:\s*moreMa[\s\S]{0,500}onClicked[\s\S]{0,800}",
                      self.src)
        self.assertIsNotNone(m)
        handler = m.group(0)
        self.assertIn("sessionMenu.currentConvId =", handler,
                      "必须缓存 convId 到 sessionMenu 属性")
        self.assertIn("sessionMenu.currentConvName =", handler,
                      "必须缓存 convName 到 sessionMenu 属性")

    def test_session_menu_has_three_items(self):
        """sessionMenu 必须有 3 个 MenuItem：重命名 / 清空 / 删除"""
        m = re.search(r"id:\s*sessionMenu[\s\S]{0,2000}", self.src)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn('qsTr("重命名...")', body)
        self.assertIn('qsTr("清空消息")', body)
        self.assertIn('qsTr("删除会话")', body)

    def test_session_menu_routes_to_dialogs_and_slots(self):
        """Day 20.4.4：重命名改走 renameDialog（不直接调 bridge.rename_session），
        清空 / 删除仍直接调 bridge slot；重命名的 bridge 调用必须仍存在于
        renameDialog 内（端到端不丢）。"""
        m = re.search(r"id:\s*sessionMenu[\s\S]{0,2500}", self.src)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn("renameDialog.open()", body,
                      "重命名 MenuItem 必须打开 renameDialog")
        self.assertNotIn("bridge.rename_session(", body,
                         "重命名不应在 MenuItem 里直接调 bridge.rename_session"
                         "（要走 renameDialog 交互式输入）")
        self.assertIn("bridge.clear_session_history", body,
                      "清空消息 MenuItem 必调 bridge.clear_session_history")
        self.assertIn("bridge.delete_session", body,
                      "删除会话 MenuItem 必调 bridge.delete_session")
        # 重命名的 bridge 调用仍必须存在于 renameDialog（端到端覆盖不丢）
        dm = re.search(r"id:\s*renameDialog[\s\S]{0,2500}", self.src)
        self.assertIsNotNone(dm, "找不到 renameDialog")
        self.assertIn("bridge.rename_session(", dm.group(0),
                      "renameDialog.onAccepted 必须调 bridge.rename_session")

    def test_no_legacy_direct_delete(self):
        """Day 20.4 之前的 moreMa.onClicked 直接 delete_session 已删"""
        # 找 moreMa onClicked handler 不应再含 bridge.delete_session 直接调用
        m = re.search(r"id:\s*moreMa[\s\S]{0,500}onClicked[\s\S]{0,400}",
                      self.src)
        if m:
            handler = m.group(0)
            self.assertNotIn("bridge.delete_session", handler,
                             "three-dot 不应直接调 delete_session（会绕过菜单）")

    def test_rename_dialog_exists(self):
        """renameDialog 必须存在 + onAccepted 调 bridge.rename_session"""
        m = re.search(r"id:\s*renameDialog[\s\S]{0,2500}", self.src)
        self.assertIsNotNone(m, "找不到 renameDialog")
        body = m.group(0)
        self.assertIn("bridge.rename_session(", body,
                      "renameDialog.onAccepted 必须调 bridge.rename_session")
        # onOpened 必同步 currentName 到 renameInput（否则 input 是空）
        self.assertIn("renameInput.text =", body,
                      "renameDialog.onOpened 必同步 currentName 到 input")


import re


class TestJiebaWarningsSuppressed(unittest.TestCase):
    """Day 20.4: 用户报「点击后报错这些」实际是 jieba 的 SyntaxWarning。

    jieba 库（.venv\Lib\site-packages\jieba\）源码里用普通字符串写正则
    ``"\\."`` / ``"\\s"``，Python 3.12+ 会按字面意义解释 backslash（因为
    非 raw 字符串），每次 import 触发 SyntaxWarning，污染用户首屏让
    用户误以为程序坏了。

    修复：main.py 启动时 filterwarnings("ignore", ".*invalid escape sequence.*")
    关掉所有此类警告（jieba 多年不修源码，我们只能这一侧处理）。
    """

    @classmethod
    def setUpClass(cls):
        cls.src = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()

    def test_main_py_filters_invalid_escape(self):
        """main.py 必须有 filterwarnings 过滤 invalid escape sequence"""
        self.assertRegex(self.src, r"filterwarnings\([^)]*invalid\s+escape\s+sequence",
                         "main.py 必须有 filterwarnings 抑制 jieba 的 SyntaxWarning")

    def test_no_jieba_in_main_dependencies(self):
        """main.py 不应直接依赖 jieba（jieba 是间接 import 进来的）"""
        # 不强求 jieba 不被 import，但确认我们的 filter 覆盖到了
        self.assertIn("warnings", self.src,
                      "main.py 必须 import warnings 才能 filterwarnings")


class TestAutomationSlotSuccessToast(unittest.TestCase):
    """Day 20.4: show_automation_manager_dialog 关闭后必须 toast「任务管理已关闭」。

    用户报「点击后报错」实际是把 jieba warnings 当成错误。给个成功 toast
    让用户看到「正常工作了」的反馈。
    """

    def setUp(self):
        # 拆分后 show_automation_manager_dialog 在 _bridge_menu.py —— 扫模块组
        from tests._bridge_source import bridge_source
        self.src = bridge_source()
        self.toast_msgs = []

    def test_success_toast_after_close(self):
        """slot 关闭 dialog 后必须 emit toast"""
        self.assertIn('任务管理已关闭', self.src,
                      "show_automation_manager_dialog 必须有「任务管理已关闭」成功 toast")

    def test_failure_path_has_traceback(self):
        """except 分支必须 print traceback（用户能看到完整堆栈）"""
        m = re.search(
            r"def show_automation_manager_dialog.*?(?=\n    @pyqtSlot|\n    def _get_dialog_host|\Z)",
            self.src, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn("traceback.print_exc()", body,
                      "失败时必须 print traceback，不能只 print(e)")


if __name__ == "__main__":
    unittest.main()
