"""ChatBridge 功能切片 —— SendMixin（发送 / 系统提示词 / 历史）。

拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
from datetime import datetime

from PyQt5.QtCore import pyqtSlot

from . import config as _config
from ._safe_path import is_safe_to_read  # Day 19.1: 路径安全检查（DRY）


class SendMixin(object):
    """send_message / 专家路由 / system_prompt / 用户 content / history 持久化。"""

    # ============ QML → Python Slot ============
    # Day 17 修复: QML 侧调用的是 send_message(text, false, paths)（3 个实参），
    # 而这里原先只声明了 @pyqtSlot(str)，QML 按元对象重载解析时匹配不到 3 参重载，
    # 发送/带图发送会直接抛 TypeError。改为注册三个重载，Python 侧按需调用也不受影响。
    @pyqtSlot(str)
    @pyqtSlot(str, bool)
    @pyqtSlot(str, bool, 'QVariantList')
    def send_message(self, text: str, use_fake: bool = False, image_paths=None):
        """QML 触发用户发送一条消息。

        Day 3-5 起默认走 real 路径（fake OpenAI client + 真 WorkerThread）。
        Day 10: use_fake=True 时跳过真 LLM（截图/单元测试用，不烧 token）。
        Day 14: image_paths - 图片附件列表（图片会转 base64 multimodal content 发送给 LLM）
        """
        if not text or not text.strip():
            return
        if self.isBusy:
            # 真实实现：QML 应已禁用发送按钮；这里做兜底
            self.appendError.emit("system", "正在生成中，请稍候")
            return
        text = text.strip()
        # Day 19 (C-NEW-3 修复): 专家前缀路由（/dev、/analyst 等）
        # 与 chat_window.on_send_message 一致
        try:
            from .expert_router import match_expert, build_system_prompt
            from . import config as _cfg
            experts = _cfg.load_experts() if hasattr(_cfg, "load_experts") else self._load_experts()
            matched_id, stripped = match_expert(text, experts)
            if matched_id:
                # Day 20.6.15: 走统一入口 _apply_expert（持久化 + emit
                # expertChanged）—— 此前只改 in-memory，用 /dev 切的专家
                # 重启即丢，QML 下拉框也不知道要跟着跳。
                self._apply_expert(matched_id)
                if stripped:
                    text = stripped
        except Exception:
            pass
        self._send_user_bubble(text)
        # Day 14: 构建 multimodal content（如果有图片附件）
        user_content = self._build_user_content(text, image_paths or [])
        # Day 19: 构建 system_prompt（专家 + 插件 skill + 工具引导）
        # 与 chat_window.on_send_message 一致
        system_prompt = self._build_system_prompt()
        self.start_real_chat(
            user_text=text,
            use_fake=use_fake,
            user_content=user_content,
            system_prompt=system_prompt,
        )

    def _load_experts(self):
        """Day 19: 懒加载 experts 字典。"""
        try:
            from .expert_router import load_experts
            return load_experts()
        except Exception:
            return {}

    def _build_system_prompt(self) -> str:
        """Day 19 (C-NEW-3 修复): 与 chat_window.on_send_message 一致构造
        专家 + 插件 skill + 工具引导。

        返回空字符串（而非 None）— 避免 start_real_chat 把它当 None 处理。
        """
        try:
            from .expert_router import build_system_prompt, resolve_settings
            from . import config as _cfg
            experts = self._load_experts()
            eid = getattr(self, "_current_expert_id", "general")
            if eid not in experts:
                eid = "general"
            expert = experts.get(eid, {})
            # 全局偏好
            enable_thinking = getattr(self, "_enable_thinking", False)
            enable_tools = getattr(self, "_enable_tools", True)
            # 插件列表（与 _enabled_plugin_names 一致）
            from . import plugin_manager
            plugins, _ = plugin_manager.discover_plugins()
            enabled = self._enabled_plugin_names()
            # 沿用 chat_window 的设置（max_rounds=3 与 QtQuick 路径固定值匹配）
            ep, use_tools, use_thinking, rounds = resolve_settings(
                expert, enabled, enable_tools, enable_thinking,
                False, 3,  # agent_mode 暂不开，max_rounds=3
            )
            if ep or (expert.get("system_prompt") or "").strip():
                agent_on = False
                sp = build_system_prompt(expert, plugins, ep, use_tools, agent_mode=agent_on)
                return sp or ""
        except Exception:
            pass
        return ""

    def _build_user_content(self, text, image_paths):
        """Day 14: 构建用户消息 content

        Day 19 (C-NEW-4 修复)：图片路径必须：
        1. 是相对路径（拒绝对路径，与 read_text_file 一致）
        2. 扩展名在 _IMAGE_EXT 白名单内
        3. 文件 < _MAX_IMAGE_BYTES
        不在白名单内 / 是绝对路径 / 读取失败 → 跳过并 print 警告
        """
        if not image_paths:
            return text
        blocks = [{"type": "text", "text": text}]
        for path in image_paths:
            # 路径安全检查（与 read_text_file 一致；Day 19 抽到 _safe_path）
            if not is_safe_to_read(path):
                print(f"[chat_bridge] 图片路径拒绝（绝对路径）: {path}")
                continue
            lower = path.lower()
            ext = None
            for e in self._IMAGE_EXT:
                if lower.endswith(e):
                    ext = e
                    break
            if ext is None:
                print(f"[chat_bridge] 图片路径拒绝（不在白名单）: {path}")
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read()
                if len(data) > self._MAX_IMAGE_BYTES:
                    print(f"[chat_bridge] 图片过大跳过 ({len(data)}B): {path}")
                    continue
                import base64
                b64 = base64.b64encode(data).decode("ascii")
                mime = self._IMAGE_MIMES[ext]
                blocks.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            except Exception as e:
                print(f"[chat_bridge] 图片读取失败 ({path}): {e}")
        return blocks

    def _send_user_bubble(self, text: str):
        ts = datetime.now().strftime("%H:%M")
        # Day 20.4.3: escape user text（防 RichText 把 ``<stdio.h>`` 误读成标签
        # 导致整段消失）。存盘还是存 raw（_append_history 第二个参数照旧），
        # 只有 emit 给 QML 的版本 escape —— reload 时再 escape 一次。
        rendered = self._render_for_qml("user", text, self._theme)
        self.messageAdded.emit("user", self._mk_msg(rendered, ts))
        # Day 10: 用户消息立即写到 SQLite（防止崩溃丢失）—— 存 raw
        self._append_history("user", text)

    def _append_history(self, role: str, content: str):
        """Day 10: 把消息 append 到当前会话 history + 写 SQLite

        - user 消息: 立即保存
        - assistant 消息: response_complete / stop / error 时由 _flush_stream_buffer 触发保存
        - 标题自动从第一条 user message 取（前 30 字，"新对话" 时才覆盖）

        Day 19 (H-NEW-7 修复)：content 超过 MAX_CONTENT_LEN 截断；
        history 超过 MAX_HISTORY_LEN 截掉最旧消息（保留最近 500 条）。
        """
        if not self._current_conv_id:
            return
        if not content or not content.strip():
            return
        # Day 19: 截断单条 content 避免 LLM 写 GB 级消息
        if len(content) > self.MAX_CONTENT_LEN:
            content = content[:self.MAX_CONTENT_LEN] + "\n\n[已截断，超出 MAX_CONTENT_LEN]"
        try:
            convs, _ = _config.load_conversations()
            conv = next((c for c in convs if c["id"] == self._current_conv_id), None)
            if conv is None:
                return
            history = conv.get("history") or []
            history.append({"role": role, "content": content})
            # Day 19: 截断 history 长度（保留最近 MAX_HISTORY_LEN 条）
            if len(history) > self.MAX_HISTORY_LEN:
                history = history[-self.MAX_HISTORY_LEN:]
            conv["history"] = history
            # 标题自动取首条 user 消息前 30 字（仅在"新对话"标题时）
            if role == "user" and conv.get("title", "新对话") == "新对话":
                conv["title"] = (content[:30] + ("..." if len(content) > 30 else "")).strip() or "新对话"
            _config.save_single_conversation(conv, self._current_conv_id)
        except Exception as e:
            print(f"[chat_bridge] 保存历史失败: {e}")

    def _do_regenerate(self, user_text: str):
        """Day 20: 重新生成走 start_real_chat 但跳过 _send_user_bubble

        user 气泡已经在 QML 端可见（sessionLoaded 重渲染后），不能重复 emit。
        走 start_real_chat 时它会发一个 ai 气泡占位 → 流式填入 → finalizeLast。
        assistant finalize 时 _flush_stream_buffer 会把内容 append 到 history（_append_history）。
        """
        text = (user_text or "").strip()
        if not text:
            return
        # 专家路由（与 send_message 一致）
        try:
            from .expert_router import match_expert
            experts = self._load_experts()
            matched_id, stripped = match_expert(text, experts)
            if matched_id:
                # Day 20.6.15: 同 send_message —— 统一入口，持久化 + emit
                self._apply_expert(matched_id)
                if stripped:
                    text = stripped
        except Exception:
            pass
        user_content = self._build_user_content(text, [])
        system_prompt = self._build_system_prompt()
        self.start_real_chat(
            user_text=text,
            use_fake=False,
            user_content=user_content,
            system_prompt=system_prompt,
        )
