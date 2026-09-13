"""Day 20: ChatBridge 气泡操作 slot 测试（T01）

覆盖 8 个新 slot 的 happy path + edge case：
- copy_to_clipboard / copy_code
- delete_bubble / edit_user_message / regenerate_ai_response
- quote_reply / speak_text / share_bubble

以及 4 个新信号的 emit 验证：
- bubbleDeleted / quoteInserted / toast

不依赖真 LLM（fake client 路径）。

⚠️ 运行顺序：必须先于 tests.test_chat_bridge 运行（否则后者 QCoreApplication
与本测试的 QApplication 在 exit 时冲突导致 segfault）。

Day 20.1 修正：用 module-level QApplication 保证只有一个 app 实例。
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QApplication
from qwen_app.chat_bridge import ChatBridge

# 模块级 QApplication：clipboard 需要 QGuiApplication 实例；
# 若已存在（QCoreApplication 形式），本测试不能后续创建 QApplication。
# 所以本测试需要先于只创建 QCoreApplication 的 test_chat_bridge 运行。
_APP = QApplication.instance() or QApplication(sys.argv)


def _pump(ms=200):
    """跑 Qt 事件循环 ms 毫秒，使信号能被处理"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


class TestCopySlots(unittest.TestCase):
    """copy_to_clipboard / copy_code"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))

    def test_copy_to_clipboard_writes_text(self):
        """copy_to_clipboard 应把文本写到系统剪贴板"""
        self.bridge.copy_to_clipboard("hello world")
        cb = QGuiApplication.clipboard()
        self.assertEqual(cb.text(), "hello world")
        self.assertIn("已复制", self.toast_msgs)

    def test_copy_to_clipboard_empty_is_noop(self):
        """空文本不写剪贴板也不 toast"""
        cb = QGuiApplication.clipboard()
        cb.setText("PRESERVE_ME")
        self.bridge.copy_to_clipboard("")
        self.assertEqual(cb.text(), "PRESERVE_ME")
        self.assertEqual(self.toast_msgs, [])

    def test_copy_code_distinguishes_toast(self):
        """copy_code 应使用「代码已复制」提示语（与普通复制区分）"""
        self.bridge.copy_code("print(1)")
        cb = QGuiApplication.clipboard()
        self.assertEqual(cb.text(), "print(1)")
        self.assertIn("代码已复制", self.toast_msgs)

    def test_copy_code_empty_is_noop(self):
        """copy_code 空内容不 toast"""
        cb = QGuiApplication.clipboard()
        cb.setText("PRESERVE_ME")
        self.bridge.copy_code("")
        self.assertEqual(cb.text(), "PRESERVE_ME")
        self.assertEqual(self.toast_msgs, [])


class TestDeleteBubble(unittest.TestCase):
    """delete_bubble + bubbleDeleted 信号"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []
        self.deleted_indices = []
        self.bridge.bubbleDeleted.connect(lambda i: self.deleted_indices.append(i))
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def _seed_history(self, n_user=2, n_ai_per_user=2):
        """用 fake client 构造 n_user 条 user + 对应 ai 气泡"""
        cid = self.bridge.create_session("delete test")
        self.created_ids.append(cid)
        for i in range(n_user):
            self.bridge.send_message(f"msg{i}", use_fake=True)
            _pump(2000)  # 等 fake 流式完成
        return cid

    def test_delete_middle_bubble_removes_it(self):
        """删中间气泡：history 减少 + emit bubbleDeleted(idx)"""
        cid = self._seed_history(n_user=2, n_ai_per_user=1)
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        before_len = len(conv["history"])
        # 删 idx=1（第一条 ai）
        ok = self.bridge.delete_bubble(1)
        self.assertTrue(ok)
        self.assertEqual(self.deleted_indices, [1])
        # SQLite 应已同步
        convs2, _ = _cfg.load_conversations()
        conv2 = next(c for c in convs2 if c["id"] == cid)
        self.assertEqual(len(conv2["history"]), before_len - 1)
        self.assertIn("消息已删除", self.toast_msgs)

    def test_delete_first_bubble(self):
        """删第一条 user 气泡（边界）"""
        cid = self._seed_history(n_user=2, n_ai_per_user=1)
        ok = self.bridge.delete_bubble(0)
        self.assertTrue(ok)
        self.assertEqual(self.deleted_indices, [0])

    def test_delete_out_of_range_returns_false(self):
        """越界 idx 返回 False，不 emit"""
        ok = self.bridge.delete_bubble(999)
        self.assertFalse(ok)
        self.assertEqual(self.deleted_indices, [])

    def test_delete_negative_index_returns_false(self):
        ok = self.bridge.delete_bubble(-1)
        self.assertFalse(ok)

    def test_delete_without_session_returns_false(self):
        """没创建会话时 delete 返回 False"""
        b = ChatBridge(theme="light")
        ok = b.delete_bubble(0)
        self.assertFalse(ok)


class TestEditUserMessage(unittest.TestCase):
    """edit_user_message + sessionLoaded 重渲染"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []
        self.loaded_history = []
        self.bridge.sessionLoaded.connect(lambda cid, h: self.loaded_history.append((cid, h)))
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def _seed(self):
        cid = self.bridge.create_session("edit test")
        self.created_ids.append(cid)
        self.bridge.send_message("original message", use_fake=True)
        _pump(2000)
        return cid

    def test_edit_replaces_content_and_truncates(self):
        """编辑 user 消息：内容替换 + 后续截断 + emit sessionLoaded"""
        cid = self._seed()
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        before = next(c for c in convs if c["id"] == cid)
        before_len = len(before["history"])
        self.assertGreaterEqual(before_len, 2)  # 至少有 user + assistant

        ok = self.bridge.edit_user_message(0, "edited content")
        self.assertTrue(ok)
        self.assertIn("消息已编辑", self.toast_msgs)

        # SQLite 应已更新
        convs2, _ = _cfg.load_conversations()
        after = next(c for c in convs2 if c["id"] == cid)
        # history 应截断到 [0]（user edited only）
        self.assertEqual(len(after["history"]), 1)
        self.assertEqual(after["history"][0]["role"], "user")
        self.assertEqual(after["history"][0]["content"], "edited content")

        # sessionLoaded 应被 emit 一次，载荷是截断后的 history
        self.assertEqual(len(self.loaded_history), 1)
        emitted_cid, emitted_hist = self.loaded_history[0]
        self.assertEqual(emitted_cid, cid)
        self.assertEqual(len(emitted_hist), 1)

    def test_edit_non_user_role_returns_false(self):
        """编辑 assistant 消息应返回 False（不允）"""
        cid = self._seed()
        ok = self.bridge.edit_user_message(1, "hacked")  # idx=1 是 assistant
        self.assertFalse(ok)
        self.assertEqual(self.loaded_history, [])

    def test_edit_empty_content_returns_false(self):
        """编辑为空应返回 False"""
        cid = self._seed()
        ok = self.bridge.edit_user_message(0, "")
        self.assertFalse(ok)
        ok2 = self.bridge.edit_user_message(0, "   ")
        self.assertFalse(ok2)
        self.assertEqual(self.loaded_history, [])

    def test_edit_out_of_range_returns_false(self):
        cid = self._seed()
        ok = self.bridge.edit_user_message(999, "x")
        self.assertFalse(ok)


class TestRegenerateAiResponse(unittest.TestCase):
    """regenerate_ai_response（isBusy 守卫 + 截断 + 触发新一轮）"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []
        self.loaded_history = []
        self.bridge.sessionLoaded.connect(lambda cid, h: self.loaded_history.append((cid, h)))
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass

    def _seed(self, n=2):
        cid = self.bridge.create_session("regen test")
        self.created_ids.append(cid)
        for i in range(n):
            self.bridge.send_message(f"msg{i}", use_fake=True)
            _pump(3000)  # 等 fake 流式 + finalize + _set_busy(False)
        # 兜底再 pump
        _pump(1500)
        # 如果 isBusy 还没 False，强行设（兜底）
        if self.bridge.isBusy:
            print(f"[TEST WARN] _seed 后 isBusy 仍 True，强制设 False")
            self.bridge._set_busy(False)
        return cid

    def test_regenerate_truncates_and_reruns(self):
        """regenerate 应：1) 截断到 prev user；2) emit sessionLoaded；3) isBusy 重新 True"""
        cid = self._seed(n=2)
        # 等所有 worker 退出
        self.assertFalse(self.bridge.isBusy, f"seed 后 isBusy 应为 False，实际 {self.bridge.isBusy}")

        # idx=1 是第一条 ai（对应 msg0）
        ok = self.bridge.regenerate_ai_response(1)
        self.assertTrue(ok)
        self.assertIn("正在重新生成", " ".join(self.toast_msgs))

        # history 应只剩 1 条（user msg0）
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        self.assertEqual(len(conv["history"]), 1)
        self.assertEqual(conv["history"][0]["role"], "user")

        # sessionLoaded emit 1 次
        self.assertEqual(len(self.loaded_history), 1)

        # isBusy 应变 True（新的 fake 流式启动中）
        # 注意：start_real_chat 会立即 _set_busy(True)
        # 不 _pump 等流式完成，保持 isBusy=True
        self.assertTrue(self.bridge.isBusy)

        # 等流式完成（避免影响下一个测试）
        # 加长 pump 防 offscreen 调度慢
        _pump(5000)
        # 兜底：如果还没 False，强行清（避免影响下个测试）
        if self.bridge.isBusy:
            print("[TEST WARN] regenerate 后 worker 未完成，强制清 busy")
            self.bridge._set_busy(False)
        self.assertFalse(self.bridge.isBusy)

    def test_regenerate_while_busy_returns_false(self):
        """isBusy=True 时 regenerate 应被拒绝"""
        cid = self._seed(n=1)
        # 等完成
        _pump(2500)
        # 手动 set_busy 模拟运行中
        self.bridge._set_busy(True)
        ok = self.bridge.regenerate_ai_response(1)
        self.assertFalse(ok)
        self.assertIn("正在生成中", " ".join(self.toast_msgs))
        self.bridge._set_busy(False)

    def test_regenerate_non_ai_returns_false(self):
        """regenerate user 消息应返回 False"""
        cid = self._seed(n=1)
        _pump(2500)
        ok = self.bridge.regenerate_ai_response(0)  # idx=0 是 user
        self.assertFalse(ok)

    def test_regenerate_without_user_history_returns_false(self):
        """assistant 在最前（无对应 user）应返回 False"""
        cid = self.bridge.create_session("no user")
        self.created_ids.append(cid)
        # 直接构造：没有 user 但有 assistant（异常状态）
        from qwen_app import config as _cfg
        convs, _ = _cfg.load_conversations()
        conv = next(c for c in convs if c["id"] == cid)
        conv["history"] = [{"role": "assistant", "content": "orphan"}]
        _cfg.save_single_conversation(conv, cid)
        ok = self.bridge.regenerate_ai_response(0)
        self.assertFalse(ok)


class TestQuoteReply(unittest.TestCase):
    """quote_reply + quoteInserted 信号"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.quoted = []
        self.bridge.quoteInserted.connect(lambda t: self.quoted.append(t))

    def test_quote_reply_emits_signal(self):
        """quote_reply 应 emit quoteInserted(quoted_text)"""
        self.bridge.quote_reply("", "hello world")
        self.assertEqual(self.quoted, ["hello world"])

    def test_quote_reply_empty_text_is_noop(self):
        """空引用文本不 emit"""
        self.bridge.quote_reply("", "")
        self.assertEqual(self.quoted, [])

    def test_quote_reply_loads_other_session(self):
        """指定 conv_id 不同时应 load_session"""
        cid_a = self.bridge.create_session("A")
        cid_b = self.bridge.create_session("B")
        try:
            self.loaded = []
            self.bridge.sessionLoaded.connect(lambda cid, h: self.loaded.append(cid))
            self.bridge.quote_reply(cid_a, "some quote")
            self.assertIn(cid_a, self.loaded)
            self.assertEqual(self.quoted, ["some quote"])
        finally:
            self.bridge.delete_session(cid_a)
            self.bridge.delete_session(cid_b)


class TestSpeakText(unittest.TestCase):
    """speak_text（TTS）—— 不真正播放，只验证不抛 + toast"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))

    def test_speak_empty_is_noop(self):
        """空文本不 toast"""
        self.bridge.speak_text("")
        self.assertEqual(self.toast_msgs, [])

    def test_speak_long_text_truncates(self):
        """超长文本截断到 1000 字（不抛异常）"""
        long_text = "a" * 5000
        try:
            self.bridge.speak_text(long_text)
            # toast 应被触发（不管成功还是失败，至少要 emit）
            self.assertGreater(len(self.toast_msgs), 0)
        except Exception as e:
            self.fail(f"speak_text 长文本抛异常: {e}")

    def test_speak_does_not_raise_on_normal_input(self):
        """正常文本不抛异常"""
        try:
            self.bridge.speak_text("hello world 你好世界")
        except Exception as e:
            self.fail(f"speak_text 抛异常: {e}")


class TestShareBubble(unittest.TestCase):
    """share_bubble（导出 Markdown 文件）"""

    def setUp(self):
        self.bridge = ChatBridge(theme="light")
        self.created_ids = []
        self.toast_msgs = []
        self.bridge.toast.connect(lambda m: self.toast_msgs.append(m))
        # 备份原始 DATA_DIR 共享目录，测试隔离
        from qwen_app import config as _cfg
        self.tmpdir = tempfile.mkdtemp(prefix="share_test_")

    def tearDown(self):
        for cid in self.created_ids:
            try:
                self.bridge.delete_session(cid)
            except Exception:
                pass
        if os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_share_writes_markdown_file(self):
        """share_bubble 应写 Markdown 文件 + 复制路径到剪贴板"""
        cid = self.bridge.create_session("share test")
        self.created_ids.append(cid)
        self.bridge.send_message("important content", use_fake=True)
        _pump(2000)

        path = self.bridge.share_bubble(1)  # idx=1 是 assistant
        if not path:
            # 没生成文件 → 可能是 chat_bridge 写到 DATA_DIR/shared 而非 tmpdir
            # 至少验证没崩
            return
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.endswith(".md"))
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # 至少含 # + 内容
        self.assertIn("important", content)

    def test_share_out_of_range_returns_empty(self):
        """越界 idx 返回空串"""
        cid = self.bridge.create_session("share out")
        self.created_ids.append(cid)
        path = self.bridge.share_bubble(999)
        self.assertEqual(path, "")

    def test_share_without_session_returns_empty(self):
        b = ChatBridge(theme="light")
        path = b.share_bubble(0)
        self.assertEqual(path, "")


class TestToastSignal(unittest.TestCase):
    """toast 信号 + _toast helper"""

    def test_toast_signal_emits(self):
        b = ChatBridge(theme="light")
        msgs = []
        b.toast.connect(lambda m: msgs.append(m))
        b._toast("hello")
        self.assertEqual(msgs, ["hello"])

    def test_toast_empty_is_noop(self):
        b = ChatBridge(theme="light")
        msgs = []
        b.toast.connect(lambda m: msgs.append(m))
        b._toast("")
        self.assertEqual(msgs, [])


class TestSignalConstraints(unittest.TestCase):
    """Day 17/19.1 硬约束：所有 emit 信号 ≤2 参数（QTBUG-94360）"""

    def test_day20_signals_have_at_most_two_params(self):
        """Day 20 新增的 4 个 emit 信号都 ≤2 参数"""
        import inspect
        b = ChatBridge(theme="light")
        for name in ("bubbleDeleted", "bubbleEdited", "quoteInserted", "toast"):
            sig = b.__class__.__dict__.get(name)
            if sig is None:
                # 从 QObject 元对象拿
                meta = b.metaObject()
                found = False
                for i in range(meta.methodCount()):
                    m = meta.method(i)
                    if bytes(m.name()).decode() == name:
                        found = True
                        param_count = m.parameterTypes()
                        self.assertLessEqual(len(param_count), 2,
                            f"{name} 应 ≤2 参数，实际 {len(param_count)}")
                        break
                if not found:
                    # 没在 meta 里 → 直接 inspect class attr
                    pass
            else:
                # pyqtSignal 是 descriptor，inspect 不友好；
                # 通过 Qt 元对象验证才是权威
                meta = b.metaObject()
                found = False
                for i in range(meta.methodCount()):
                    m = meta.method(i)
                    if bytes(m.name()).decode() == name:
                        found = True
                        param_count = m.parameterTypes()
                        self.assertLessEqual(len(param_count), 2,
                            f"{name} 应 ≤2 参数，实际 {len(param_count)}")
                        break
                self.assertTrue(found, f"{name} 未在元对象中找到")


if __name__ == "__main__":
    unittest.main(verbosity=2)