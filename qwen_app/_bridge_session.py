"""ChatBridge 功能切片 —— SessionMixin（会话管理）。


拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
from PyQt5.QtCore import pyqtSlot

from . import config as _config


class SessionMixin(object):
    """会话列表 / 新建 / 删除 / 重命名 / 清空 / 加载。"""

    # ============ Day 8: 会话管理 Slot ============
    @pyqtSlot(result='QVariantList')
    def list_sessions(self):
        """返回会话列表 [{id, name, time, sel}, ...] - QML 调用填入侧边栏"""
        try:
            convs, current_id = _config.load_conversations()
        except Exception:
            return []
        out = []
        for c in convs:
            out.append({
                "id": c["id"],
                "name": c.get("title") or "新对话",
                "time": self._relative_time_str(c.get("created_at", "")),
                "sel": c["id"] == (current_id or self._current_conv_id),
            })
        return out

    @pyqtSlot(str, result=str)
    def create_session(self, title="新对话"):
        """创建新会话，返回新 id；同时设为当前会话

        Day 19.1.1: 使用完整 UUID（36 字符）而非 8 字符截断。
        8 字符 hex 仅 32 bits 熵（约 4B 种可能），约 65k 个 session
        就有 50% 碰撞概率。完整 UUID v4 有 122 bits 熵，碰撞概率
        实际为零。SQLite 主键为 TEXT 无长度限制，QML 侧边栏显示
        model.name（不是 model.id），无显示侧影响。
        """
        import uuid as _uuid
        from datetime import datetime as _dt
        new_conv = {
            "id": str(_uuid.uuid4()),
            "title": title or "新对话",
            "history": [],
            "created_at": _dt.now().isoformat(),
        }
        _config.save_single_conversation(new_conv, new_conv["id"])
        self._current_conv_id = new_conv["id"]
        self.sessionListChanged.emit()
        return new_conv["id"]

    @pyqtSlot(str)
    def delete_session(self, conv_id):
        """删除会话；如果删的是当前会话则回退到第一个 + 清空右侧内容。

        Day 20.6.20 修复：删当前会话时原只 emit sessionListChanged，右侧
        messageModel 仍展示旧会话内容（直到用户手动点别的会话才刷新）。
        现在按 clear_session_history 的同模式，在删的是当前会话时 emit
        sessionLoaded(空 list) 让 QML 清空 messageModel；删除且 new_cur
        非空时则 emit sessionLoaded(new_cur, new_history) 让 QML 加载
        切换目标会话的内容（与 list_sessions 切换逻辑一致）。
        """
        if not conv_id:
            return
        try:
            convs, current_id = _config.load_conversations()
        except Exception:
            return
        was_current = (conv_id == (current_id or self._current_conv_id))
        convs = [c for c in convs if c["id"] != conv_id]
        if was_current:
            # 删当前会话：current 回退到剩下的第一条；右侧内容由 emit 同步
            new_cur = convs[0]["id"] if convs else None
        else:
            new_cur = current_id
        _config.save_conversations(convs, new_cur)
        self._current_conv_id = new_cur
        self.sessionListChanged.emit()
        if was_current:
            # 与 load_session 同语义：传 (conv_id, history) 让 QML 填右侧；
            # 无目标会话则传 (None, []) 让 QML 清空
            if new_cur:
                target = next((c for c in convs if c["id"] == new_cur), None)
                hist = (target or {}).get("history", [])
                self.sessionLoaded.emit(new_cur, hist)
            else:
                self.sessionLoaded.emit("", [])

    @pyqtSlot(str, str, result=bool)
    def rename_session(self, conv_id, new_title):
        """Day 20.4: 重命名会话。空字符串 / 仅空白 → 拒绝并 toast。

        - 单条 write（不重写全表），跟 delete_bubble 一致
        - 成功后 emit sessionListChanged 让 QML 刷新侧边栏
        """
        new_title = (new_title or "").strip()
        if not new_title:
            self._toast("会话名不能为空")
            return False
        if not conv_id:
            return False
        try:
            convs, _ = _config.load_conversations()
        except Exception:
            return False
        target = next((c for c in convs if c["id"] == conv_id), None)
        if target is None:
            self._toast("找不到该会话")
            return False
        target["title"] = new_title
        _config.save_single_conversation(target, conv_id)
        self.sessionListChanged.emit()
        return True

    @pyqtSlot(str, result=bool)
    def clear_session_history(self, conv_id):
        """Day 20.4: 清空指定会话的历史（保留会话本身）

        - 等价于 PyQt5 路径的「清空消息」按钮（chat_window 行为）
        - 如果清的是当前会话，emit sessionLoaded(空 list) 让 QML 清空 messageModel
        """
        if not conv_id:
            return False
        try:
            convs, _ = _config.load_conversations()
        except Exception:
            return False
        target = next((c for c in convs if c["id"] == conv_id), None)
        if target is None:
            self._toast("找不到该会话")
            return False
        target["history"] = []
        _config.save_single_conversation(target, conv_id)
        if self._current_conv_id == conv_id:
            # 当前会话被清空 → QML 端 messageModel 也得清
            self.sessionLoaded.emit(conv_id, [])
        self._toast("已清空会话消息")
        return True

    @pyqtSlot(str)
    def load_session(self, conv_id):
        """加载会话历史 -> emit sessionLoaded(conv_id, history) -> QML 重填 messageModel

        Day 20.4 修复：必须同时
          1) 更新 _config 的 current_id（list_sessions 用这个判断 sel）
          2) emit sessionListChanged（让侧边栏 highlight 移到新会话）
        否则点新会话 → messageModel 更新但蓝色高亮仍停在旧会话，用户
        视觉上感觉「没切」。
        """
        if not conv_id:
            return
        try:
            convs, current_id = _config.load_conversations()
        except Exception:
            return
        conv = next((c for c in convs if c["id"] == conv_id), None)
        if not conv:
            return
        self._current_conv_id = conv_id
        # 持久化 current_id（让 list_sessions().sel 反映新选择）
        # Day 20.4.5: 只更新 current_id！**不要**用 save_conversations() 全量重写。
        # 原因：① save_conversations 会把每行的 updated_at 写成
        #   conv.get("updated_at", conv.get("created_at",""))，而
        #   load_conversations() 返回的 dict 里**没有 updated_at** → 回退成
        #   created_at → **把排序键抹平成 created_at**，而侧边栏正是
        #   `ORDER BY updated_at DESC` → 点一下会话整个列表可能重排
        #   （用户看到"列表自己跳了"）。② 每次点会话重写 185 行纯属浪费。
        # set_current_conversation() 只动 session_state.current_id，无副作用。
        if current_id != conv_id:
            try:
                _config.set_current_conversation(conv_id)
            except Exception:
                pass  # 持久化失败不影响加载
        # history 字段是 [{role: 'user'|'assistant'|'system', content: '...'}]
        raw_history = conv.get("history", []) or []
        # Day 20.4.3: 每条 history 都先经 _render_for_qml 处理（user escape +
        # assistant markdown_to_html），保证 QML Text (RichText) 显示正确。
        # 不处理的话：AI markdown 不渲染 + user 含 ``<`` 时 RichText 解析失败整段消失
        rendered_history = []
        for h in raw_history:
            if not h:
                continue
            role = h.get("role", "user")
            content = h.get("content", "")
            rendered_history.append({
                "role": role,
                "content": self._render_for_qml(role, content, self._theme),
            })
        self.sessionLoaded.emit(conv_id, rendered_history)
        # 通知侧边栏 highlight 跟随移动
        self.sessionListChanged.emit()

    @staticmethod
    def _relative_time_str(iso_str):
        """相对时间文本：今天 HH:MM / 昨天 / N 天前 / YYYY-MM-DD"""
        if not iso_str:
            return ""
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(iso_str)
            now = _dt.now()
            diff = now - dt
            if diff.days == 0:
                return "今天 " + dt.strftime("%H:%M")
            elif diff.days == 1:
                return "昨天"
            elif diff.days < 7:
                return f"{diff.days} 天前"
            else:
                return dt.strftime("%Y-%m-%d")
        except Exception:
            return iso_str[:10]
