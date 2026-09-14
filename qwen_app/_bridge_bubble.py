"""ChatBridge 功能切片 —— BubbleMixin（气泡操作）。

拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
import os

from PyQt5.QtCore import pyqtSlot

from . import config as _config


# ============ Day 20: 气泡操作 Slot（被 MessageBubble 三点菜单 / Main MenuBar 触发）============
# 8 个 slot 覆盖：复制 / 复制代码 / 删除 / 编辑用户消息 / 重新生成 / 引用回复 / TTS / 分享导出
#
# 设计约束：
# 1. 槽函数本身不限参数（QTBUG-94360 只约束 Python 信号）
# 2. emit 信号全部 ≤2 参数；多字段用 QVariantMap
# 3. 每个 slot 失败时静默 print + 走 _toast 提示，不抛异常
# 4. 操作持久化走 _config.save_single_conversation（不重写全表）

class BubbleMixin(object):
    """气泡操作 slot：复制 / 删除 / 编辑 / 重新生成 / 引用 / 朗读 / 分享 / toast。"""

    @pyqtSlot(str)
    def copy_to_clipboard(self, text: str):
        """Day 20: 复制文本到系统剪贴板（菜单「复制」触发）"""
        if not text:
            return
        try:
            from PyQt5.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            cb.setText(text)
            self._toast("已复制")
        except Exception as e:
            print(f"[chat_bridge] copy_to_clipboard 失败: {e}")
            self._toast("复制失败")

    @pyqtSlot(str)
    def copy_code(self, code: str):
        """Day 20: 复制代码块（带 toast 区分于普通复制）"""
        if not code:
            return
        try:
            from PyQt5.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(code)
            self._toast("代码已复制")
        except Exception as e:
            print(f"[chat_bridge] copy_code 失败: {e}")
            self._toast("复制失败")

    @pyqtSlot(int, result=bool)
    def delete_bubble(self, msg_index: int) -> bool:
        """Day 20: 删除当前会话的 msg_index 气泡

        - 同步改 SQLite history（单条写入，不重写全表）
        - emit bubbleDeleted(msg_index) 让 QML 从 messageModel 同步移除
        - 返回是否成功（边界检查：越界/无效 index 返回 False）
        """
        if not self._current_conv_id:
            self._toast("无当前会话")
            return False
        if msg_index < 0:
            return False
        try:
            convs, _ = _config.load_conversations()
            conv = next((c for c in convs if c["id"] == self._current_conv_id), None)
            if conv is None:
                return False
            history = conv.get("history") or []
            if msg_index >= len(history):
                return False
            deleted_role = history[msg_index].get("role", "?")
            history.pop(msg_index)
            conv["history"] = history
            _config.save_single_conversation(conv, self._current_conv_id)
            self.bubbleDeleted.emit(msg_index)
            self._toast("消息已删除")
            print(f"[chat_bridge] delete_bubble idx={msg_index} role={deleted_role}")
            return True
        except Exception as e:
            print(f"[chat_bridge] delete_bubble 失败: {e}")
            self._toast("删除失败")
            return False

    @pyqtSlot(int, str, result=bool)
    def edit_user_message(self, msg_index: int, new_text: str) -> bool:
        """Day 20: 编辑用户消息 + 截断后续 history

        - 验证 msg_index 处 role == "user"
        - content 替换为 new_text（strip + 非空校验）
        - history 截断到 msg_index（含），丢弃其后的 ai / tool_call / tool_result
        - emit sessionLoaded 让 QML 重渲染整个会话（最简单可靠）
        """
        if not self._current_conv_id:
            self._toast("无当前会话")
            return False
        if not new_text or not new_text.strip():
            self._toast("内容不能为空")
            return False
        try:
            convs, _ = _config.load_conversations()
            conv = next((c for c in convs if c["id"] == self._current_conv_id), None)
            if conv is None:
                return False
            history = conv.get("history") or []
            if msg_index < 0 or msg_index >= len(history):
                return False
            item = history[msg_index]
            if item.get("role") != "user":
                self._toast("只能编辑用户消息")
                return False
            # 替换 content 并截断后续
            new_content = new_text.strip()
            history[msg_index] = {"role": "user", "content": new_content}
            history = history[:msg_index + 1]
            conv["history"] = history
            # 标题可能需要重取（仅在原标题由首条 user 自动派生时）
            if conv.get("title", "新对话") == new_content[:30] + ("..." if len(new_content) > 30 else ""):
                # 标题与首条 user 联动时刷新
                pass  # 保持旧标题，避免歧义
            _config.save_single_conversation(conv, self._current_conv_id)
            # 重渲染整个会话（最简单）
            self.sessionLoaded.emit(self._current_conv_id, history)
            self._toast("消息已编辑")
            print(f"[chat_bridge] edit_user_message idx={msg_index} new_len={len(new_content)}")
            return True
        except Exception as e:
            print(f"[chat_bridge] edit_user_message 失败: {e}")
            self._toast("编辑失败")
            return False

    @pyqtSlot(int, result=bool)
    def regenerate_ai_response(self, msg_index: int) -> bool:
        """Day 20: 重新生成 AI 回复

        - 验证 msg_index 处 role == "assistant"
        - 向前找最近的 user 消息作为新 prompt
        - 截断 history 到该 user（含），丢弃其后的 ai/tool_call/tool_result
        - emit sessionLoaded 重渲染
        - 调用 _do_regenerate 启动新 WorkerThread（不重新发送 user 气泡）
        """
        if self.isBusy:
            self._toast("正在生成中，请稍候")
            return False
        if not self._current_conv_id:
            self._toast("无当前会话")
            return False
        try:
            convs, _ = _config.load_conversations()
            conv = next((c for c in convs if c["id"] == self._current_conv_id), None)
            if conv is None:
                return False
            history = conv.get("history") or []
            if msg_index < 0 or msg_index >= len(history):
                return False
            item = history[msg_index]
            if item.get("role") != "assistant":
                self._toast("只能重新生成 AI 回复")
                return False
            # 向前找最近的 user 消息
            prev_user_idx = -1
            prev_user_text = ""
            for i in range(msg_index - 1, -1, -1):
                if history[i].get("role") == "user":
                    prev_user_idx = i
                    prev_user_text = history[i].get("content", "")
                    break
            if prev_user_idx < 0:
                self._toast("找不到对应的用户消息，无法重新生成")
                return False
            # 截断 history 到 prev_user_idx（含）
            history = history[:prev_user_idx + 1]
            conv["history"] = history
            _config.save_single_conversation(conv, self._current_conv_id)
            # 重渲染（QML 清 messageModel + 重填 user）
            self.sessionLoaded.emit(self._current_conv_id, history)
            # 触发新一轮生成
            self._do_regenerate(prev_user_text)
            self._toast("正在重新生成...")
            print(f"[chat_bridge] regenerate_ai_response msg_idx={msg_index} user_idx={prev_user_idx}")
            return True
        except Exception as e:
            print(f"[chat_bridge] regenerate_ai_response 失败: {e}")
            self._toast("重新生成失败")
            return False

    @pyqtSlot(str, str)
    def quote_reply(self, conv_id: str, quoted_text: str):
        """Day 20: 引用回复（把引用文本发给 QML 填到输入框）

        - emit quoteInserted(quoted_text) QML 监听后插入 inputField
        - 若 conv_id 与当前会话不同，先 load_session
        """
        text = quoted_text or ""
        if not text:
            return
        if conv_id and conv_id != self._current_conv_id:
            # 切换到目标会话（load_session 内部会 emit sessionLoaded）
            self.load_session(conv_id)
        self.quoteInserted.emit(text)
        self._toast("已引用到输入框")

    @pyqtSlot(str)
    def speak_text(self, text: str):
        """Day 20: TTS 朗读（Windows SAPI 优先；无 win32com 退化为打印）

        - 截断到 1000 字符防过长
        - 不阻塞主线程（SAPI.SpVoice.Speak 是同步调用，但 1000 字一般 < 10s）
        """
        if not text:
            return
        text_clean = text[:1000]
        try:
            import sys as _sys
            if _sys.platform.startswith("win"):
                try:
                    import win32com.client  # type: ignore
                    sp = win32com.client.Dispatch("SAPI.SpVoice")
                    sp.Speak(text_clean)
                    self._toast("朗读完成")
                    return
                except ImportError:
                    pass
                except Exception:
                    pass
            # 非 Windows 或 SAPI 不可用：退化为打印
            print(f"[TTS] {text_clean}")
            self._toast("TTS 不可用，已打印到日志")
        except Exception as e:
            print(f"[chat_bridge] speak_text 失败: {e}")
            self._toast("朗读失败")

    @pyqtSlot(int, result=str)
    def share_bubble(self, msg_index: int) -> str:
        """Day 20: 导出气泡到 Markdown 文件

        - 文件：data/shared_<conv_id_前8位>_<idx>_<时间戳>.md
        - 内容：# 角色\n\n> content\n
        - 同时把文件路径复制到剪贴板
        - 返回文件路径（失败返回空串）
        """
        if not self._current_conv_id:
            self._toast("无当前会话")
            return ""
        try:
            convs, _ = _config.load_conversations()
            conv = next((c for c in convs if c["id"] == self._current_conv_id), None)
            if conv is None:
                return ""
            history = conv.get("history") or []
            if msg_index < 0 or msg_index >= len(history):
                return ""
            item = history[msg_index]
            role_cn = {"user": "我", "assistant": "AI", "system": "系统"}.get(item.get("role", ""), "?")
            content = item.get("content", "")
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            # 输出目录：data/shared/（与 model_config.json 同根）
            out_dir = os.path.join(_config.DATA_DIR, "shared")
            os.makedirs(out_dir, exist_ok=True)
            fname = f"shared_{self._current_conv_id[:8]}_{msg_index}_{ts}.md"
            out_path = os.path.join(out_dir, fname)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"# {role_cn} @ {ts}\n\n> {content}\n")
            # 复制路径到剪贴板
            try:
                from PyQt5.QtGui import QGuiApplication
                QGuiApplication.clipboard().setText(out_path)
            except Exception:
                pass
            self._toast(f"已导出 → {fname}")
            print(f"[chat_bridge] share_bubble -> {out_path}")
            return out_path
        except Exception as e:
            print(f"[chat_bridge] share_bubble 失败: {e}")
            self._toast("导出失败")
            return ""

    def _toast(self, msg: str):
        """Day 20: 触发 toast 信号（QML 端弹一个非阻塞的小提示）"""
        if not msg:
            return
        self.toast.emit(msg)
