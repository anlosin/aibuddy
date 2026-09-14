"""ChatBridge — Python ↔ QML 双向通信桥。

阶段 2 Day 1-2：mock 路径（QML ↔ Python 信号骨架）
阶段 2 Day 3-5：real_worker 路径（用 fake OpenAI client 启动 WorkerThread，
                 把 chunk_received/response_complete/error_occurred
                 桥到 QML）

QML 端不需要知道走的是 mock 还是 real 路径，看到的都是：
- send_message(text) — 用户发送
- messageAdded(who, payload) — 新气泡（payload = {text, ts, code}）
- appendToLast(who, content) — 流式追加
- finalizeLast(who) — 完成
- appendError(who, text) — 错误气泡
- themeChanged(name) — 主题切换

⚠️ Day 17 重要约束（QTBUG-94360）
--------------------------------------------------
Qt 5.15.2（PyQt5 5.15.x 所绑定的版本，修复版本为 Qt 6.0）存在缺陷：
QML 的 `Connections { target: <pythonObject> }` 去连接**参数 ≥3 个**的信号时，
会在 QQmlConnections::connectSignalsToMethods → QQmlBoundSignalExpression →
QV4::Function::updateInternalClass 路径上发生栈越界，表现为启动期随机
0xC0000005（ACCESS_VIOLATION）或 0xC0000409（STACK_BUFFER_OVERRUN），
且崩溃概率随 QML 体量增大而升高（极易被误判为「渲染 bug」）。

参考：QTBUG-94360 / QTBUG-101264 / QTBUG-104464

因此本文件里**所有可能被 QML Connections 监听的信号，参数个数必须 ≤2**。
需要传更多字段时，把多余字段打包成 QVariantMap / QVariantList 放在第 2 个参数里。
"""
from datetime import datetime
from types import SimpleNamespace
import os
import threading
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty, QTimer, QFileSystemWatcher, Qt

from .worker import WorkerThread
from . import config as _config
from . import chat_render  # A1: 共用渲染抽象层（QtQuick + PyQt5 两条路径都走这里）
from ._safe_path import is_safe_to_read  # Day 19.1: 路径安全检查（DRY）
from .settings_dialog import show_settings, show_model_manager, show_plugin_manager  # Day 20.1: QtQuick 调 PyQt5 对话框
from ._dialog_host import _DialogHost  # Day 20.2: PyQt5 对话框需要 QWidget + 状态，bridge 是 QObject，用 host 适配


class ChatBridge(QObject):

    # ============ Python → QML 信号 ============
    # ⚠️ 会被 QML Connections 监听的信号：参数必须 ≤2 个（见文件头 QTBUG-94360 说明）
    messageAdded = pyqtSignal(str, 'QVariantMap')          # who, {text, ts, code}
    appendToLast = pyqtSignal(str, str)                    # who, content
    finalizeLast = pyqtSignal(str)                          # who
    appendError = pyqtSignal(str, str)                      # who, text
    themeChanged = pyqtSignal(str)                          # "light" | "dark"
    messageReplaced = pyqtSignal(str, str)                  # who, rendered_html (Day 6)
    # Day 8: 会话管理
    sessionListChanged = pyqtSignal()                            # 会话列表变化（QML 重拉）
    sessionLoaded = pyqtSignal(str, 'QVariantList')              # conv_id, history list
    currentModelNameChanged = pyqtSignal(str)                   # Day 10: 切换模型名名发生变化
    # Day 11: 工具调用（Day 19.1 修订：toolCallResult 3 参数触发 QTBUG-94360
    # 栈越界 —— QML Connections 监听 ≥3 参数信号会在启动期随机 0xC0000005。
    # 改为 2 参数 + QVariantMap 负载，与 messageAdded 同模板）
    toolCallStarted = pyqtSignal(str, str)                       # (工具名, 参数JSON)
    toolCallResult = pyqtSignal(str, 'QVariantMap')             # (工具名, {args, result})
    # Day 20: 气泡操作信号（被 MessageBubble.qml 三点菜单 / Main.qml MenuBar 触发）
    # 所有 emit 信号仍遵守 ≤2 参数硬约束（QTBUG-94360）
    bubbleDeleted = pyqtSignal(int)                              # msg_index —— QML 删 messageModel[idx]
    bubbleEdited = pyqtSignal(int, str)                          # msg_index, new_text —— 兜底用，主路径走 sessionLoaded
    quoteInserted = pyqtSignal(str)                              # quoted_text —— QML 填到 inputField
    toast = pyqtSignal(str)                                      # 提示语 —— QML 渲染 toast 不阻塞消息流

    # Day 18: read_text_file 接受的扩展名白名单（C3 安全修复）。
    # 限制为文本/代码/配置/文档类；拒绝二进制、可执行、压缩包、密钥/凭据类。
    _READABLE_EXT = (
        '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.json5', '.md', '.rst', '.txt',
        '.log', '.html', '.htm', '.css', '.scss', '.less',
        '.sh', '.bash', '.zsh', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf', '.env',
        '.xml', '.svg', '.csv', '.tsv', '.sql',
        '.java', '.kt', '.scala', '.go', '.rs', '.rb', '.php', '.pl',
        '.cpp', '.cxx', '.cc', '.c', '.h', '.hpp', '.m', '.mm', '.cs',
        '.lua', '.vim', '.dockerfile', '.gitignore', '.gitattributes',
        '.gradle', '.properties',
    )

    # ============ 内部状态（暴露给测试/QML 读）============
    busyChanged = pyqtSignal(bool)                          # Day 7: 发送/停止按钮切换的 NOTIFY 信号
    # Day 17 修复: NOTIFY 信号必须**不带参数**。原先 toolCallInProgressChanged
    # 声明成 pyqtSignal(bool, str) 又被两个属性当作 notify= 使用，会在元对象里
    # 留下「带参数的通知信号」这种畸形表项；QML 的 Connections 在创建时会枚举
    # 目标对象的元对象，踩到该表项后栈内存越界 → 启动偶发 0xC0000005 /
    # 0xC0000409（STATUS_STACK_BUFFER_OVERRUN）。
    toolCallInProgressChanged = pyqtSignal()                # Day 14/17: 工具调用进度 NOTIFY
    currentToolNameChanged = pyqtSignal()                   # Day 17: 工具名 NOTIFY
    pluginReloaded = pyqtSignal(int)                        # Day 14: 插件热更新事件（参数=插件数）
    pluginReloadFailed = pyqtSignal(str)                   # Day 18 (M7): 重载失败时把错误带回来

    def _get_busy(self) -> bool:
        return self._is_busy

    def _set_busy(self, busy: bool):
        if busy != self._is_busy:
            self._is_busy = busy
            self.busyChanged.emit(busy)

    isBusy = pyqtProperty(bool, _get_busy, _set_busy, notify=busyChanged)

    def _get_tool_in_progress(self) -> bool:
        return getattr(self, "_is_tool_in_progress", False)
    def _get_current_tool_name(self) -> str:
        return getattr(self, "_current_tool_name", "")
    def _set_tool_call_in_progress(self, in_progress: bool, name: str = ""):
        """Day 14: 设工具调用进度（QML 顶栏显示"正在调用工具"）"""
        prev = getattr(self, "_is_tool_in_progress", False)
        prev_name = getattr(self, "_current_tool_name", "")
        if in_progress != prev:
            self._is_tool_in_progress = in_progress
            self.toolCallInProgressChanged.emit()
        if name != prev_name:
            self._current_tool_name = name
            self.currentToolNameChanged.emit()
    toolCallInProgress = pyqtProperty(bool, _get_tool_in_progress, notify=toolCallInProgressChanged)
    currentToolName = pyqtProperty(str, _get_current_tool_name, notify=currentToolNameChanged)

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self._theme = theme
        self._is_busy = False   # Day 7: pyqtProperty backend (use _set_busy to update)
        self._last_who = None      # 当前正在流式的气泡 who
        # Day 6: 累积 buffer（按 who 维度），finalize 时用 markdown_to_html 渲染
        self._stream_buffers = {}
        self._worker = None  # Day 7: 在初始化时就设为 None（避免 stop_chat 报 AttributeError）
        # Day 18 (H1)：当前正在跑的 worker 数。reload_plugins 与正在执行的
        # worker 存在数据竞争（worker 持有 plugin module 对象引用，reload
        # 会从 sys.modules 删 module，对象变成 unbound，工具调用会抛
        # AttributeError）；reload 检测到 > 0 时推迟重试。
        self._worker_count = 0
        self._worker_count_lock = threading.Lock()
        # Day 9: 立即读当前模型名（QML 顶栏显示用）
        self._last_model_name = "(未选择)"
        try:
            from . import config as _cfg
            _m = _cfg.get_current_model()
            if _m:
                self._last_model_name = _m.get("name") or _m.get("model_id", "?")
        except Exception:
            pass
        # Day 19: 全局偏好（enable_thinking / enable_tools）从 cfg 顶层读
        # 不绑在 model dict 上 —— 与 chat_window._load_settings 一致
        try:
            _cfg_full = _cfg.load_config()
            self._enable_thinking = bool(_cfg_full.get("enable_thinking", False))
            self._enable_tools = bool(_cfg_full.get("enable_tools", True))
        except Exception:
            self._enable_thinking = False
            self._enable_tools = True
        # Day 19 (C-NEW-3 修复): 当前激活的专家（默认 "general"）。
        # 用户在 QML 端通过 set_expert 切换；/dev 前缀路由会临时改
        self._current_expert_id = "general"
        # Day 14: 插件热更新 watcher
        self._plugin_watcher = None
        # Day 8: 当前会话 ID（main.py 启动日志读这个）
        self._current_conv_id = None
        self._init_plugin_watcher()

    def _init_plugin_watcher(self):
        """Day 14: 监听 plugins 目录变化"""
        try:
            from .plugin_manager import PLUGINS_DIR
            if not os.path.isdir(PLUGINS_DIR):
                return
            self._plugin_watcher = QFileSystemWatcher([PLUGINS_DIR])
            self._plugin_watcher.directoryChanged.connect(self._on_plugin_dir_changed)
        except Exception as e:
            print(f"[chat_bridge] 初始化 plugin watcher 失败: {e}")

    @pyqtSlot()
    def enable_plugin_watcher(self):
        """Day 14: main.py QML Component.onCompleted 调这个启用 watcher

        单元测试不需要 watcher（在 __init__ 跑 reload_modules 慢且影响测试），
        推迟到 Qt 事件循环就绪后再启用。
        """
        if self._plugin_watcher is None:
            self._init_plugin_watcher()

    def _on_plugin_dir_changed(self, path):
        """Day 14: 插件目录变化时触发热重载（防抖 300ms）"""
        # Day 15: 复用 timer（避免频繁创建）
        if not hasattr(self, "_plugin_reload_timer") or self._plugin_reload_timer is None:
            self._plugin_reload_timer = QTimer()
            self._plugin_reload_timer.setSingleShot(True)
            self._plugin_reload_timer.timeout.connect(self.reload_plugins)
        else:
            self._plugin_reload_timer.stop()
        self._plugin_reload_timer.start(300)

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
                self._current_expert_id = matched_id
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

    # Day 19 (C-NEW-4 修复): 图片附件白名单 + 路径校验
    # 防止 LLM 通过 QML DropArea 拖入任意路径的 4MB 内文件（如
    # ~/.ssh/id_rsa、token 文件、.env 等）被读 + base64 编码 + 发给 LLM
    # 仅允许图片扩展名（与 read_text_file 的 _READABLE_EXT 同模板）
    _IMAGE_EXT = (
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    )
    _IMAGE_MIMES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }
    _MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB / 张
    # Day 19 (H-NEW-5 修复): 流式累积 buffer 上限。恶意/失控 LLM 服务端
    # 可无限流式响应，累积 _stream_buffers[who] 可 OOM。
    # 超过 1MB 后只保留最后 1MB（裁掉前段，避免信息丢失一半
    # —— 实际一般 1MB ≈ 200k 中文字，已远超上下文需求）。
    MAX_STREAM_BUFFER = 1 * 1024 * 1024
    # Day 19 (H-NEW-7 修复): history 大小/条数限制，防止恶意/失控 LLM
    # 返回 GB 级 content 把 SQLite 撑爆。
    MAX_CONTENT_LEN = 100 * 1024          # 100KB / 条
    MAX_HISTORY_LEN = 500                 # 最多 500 条消息（≈ 50MB 文本上限）

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

    @pyqtSlot(str)
    def send_message_mock(self, text: str):
        """Day 1-2 用的 mock 路径（无 WorkerThread，纯 QTimer 推流式）。"""
        if not text or not text.strip():
            return
        self._send_user_bubble(text.strip())
        QTimer.singleShot(500, lambda: self._simulate_ai_reply(text))

    @pyqtSlot(str)
    def set_theme(self, name: str):
        if name in ("light", "dark") and name != self._theme:
            self._theme = name
            self.themeChanged.emit(name)

    @pyqtSlot(result=str)
    def get_theme(self) -> str:
        return self._theme

    @pyqtSlot()
    def stop_chat(self):
        """Day 7: QML 调用的停止生成 - 设停止信号 + 封口当前气泡 + 等线程退出

        不主动 _set_busy(False)，让 QThread.finished -> _on_worker_finished 兑底
        不立即 self._worker = None（避免 race: worker 线程还在跑时已清空）
        """
        if self._worker is None:
            return
        self._worker.stop()
        # Day 7: 当前正在流式的气泡要封口（finalize + flush markdown 渲染）
        if self._last_who:
            self._flush_stream_buffer(self._last_who)
            self.finalizeLast.emit(self._last_who)
            self._last_who = None
        # 等线程退出（最多 200ms — 太久会冻 UI，太短 worker 来不及清理）
        # Day 14: stop 后流的最后 chunk 会触发 _on_worker_complete → _set_busy(False)
        # 这边只兜底等一下，不强制清 busy（避免双重清理）
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(200)

    # ============ Day 20: 气泡操作 Slot（被 MessageBubble 三点菜单 / Main MenuBar 触发）============
    # 8 个 slot 覆盖：复制 / 复制代码 / 删除 / 编辑用户消息 / 重新生成 / 引用回复 / TTS / 分享导出
    #
    # 设计约束：
    # 1. 槽函数本身不限参数（QTBUG-94360 只约束 Python 信号）
    # 2. emit 信号全部 ≤2 参数；多字段用 QVariantMap
    # 3. 每个 slot 失败时静默 print + 走 _toast 提示，不抛异常
    # 4. 操作持久化走 _config.save_single_conversation（不重写全表）

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
                self._current_expert_id = matched_id
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
        """删除会话；如果删的是当前会话则回退到第一个"""
        try:
            convs, current_id = _config.load_conversations()
        except Exception:
            return
        convs = [c for c in convs if c["id"] != conv_id]
        new_cur = current_id if current_id != conv_id else (convs[0]["id"] if convs else None)
        _config.save_conversations(convs, new_cur)
        if self._current_conv_id == conv_id:
            self._current_conv_id = new_cur
        self.sessionListChanged.emit()

    @pyqtSlot(str)
    def load_session(self, conv_id):
        """加载会话历史 -> emit sessionLoaded(conv_id, history) -> QML 重填 messageModel"""
        try:
            convs, _ = _config.load_conversations()
        except Exception:
            return
        conv = next((c for c in convs if c["id"] == conv_id), None)
        if not conv:
            return
        self._current_conv_id = conv_id
        # history 字段是 [{role: 'user'|'assistant'|'system', content: '...'}]
        history = conv.get("history", []) or []
        self.sessionLoaded.emit(conv_id, history)

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

    # ============ Day 10: 模型切换 Slot ============
    @pyqtSlot(result='QVariantList')
    def list_models(self):
        """Day 10: 返回可选模型列表 [{id, name, current}, ...]"""
        try:
            models, current_id = _config.load_models()
        except Exception:
            return []
        out = []
        for m in models:
            out.append({
                "id": m.get("id", ""),
                "name": m.get("name") or m.get("model_id") or "?",
                "current": m.get("id") == current_id,
            })
        return out

    @pyqtSlot(str, result=bool)
    def set_current_model(self, model_id: str) -> bool:
        """Day 10: 切换当前模型。成功返回 True。"""
        if not model_id:
            return False
        try:
            models, _ = _config.load_models()
            if not any(m.get("id") == model_id for m in models):
                return False
            _config.save_models(models, model_id)
            # 刷新 _last_model_name，后续 start_real_chat 会用新模型
            new_model = next((m for m in models if m.get("id") == model_id), None)
            if new_model:
                self._last_model_name = new_model.get("name") or new_model.get("model_id", "?")
                self.currentModelNameChanged.emit(self._last_model_name)
            return True
        except Exception as e:
            print(f"[chat_bridge] set_current_model 失败: {e}")
            return False

    @pyqtSlot(result=str)
    def get_current_model_name(self) -> str:
        """Day 10: 返回当前模型名称（QML 顶栏显示用）"""
        return self._last_model_name or "(未选择)"

    @pyqtSlot(bool, bool, result=bool)
    def set_preferences(self, enable_thinking: bool, enable_tools: bool) -> bool:
        """Day 19 (M-NEW-5 修复): 写全局偏好到 cfg 顶层 + 立即生效。

        QML 端「偏好设置」对话框（Main.qml）保存时调此 slot。
        成功返回 True，失败返回 False。
        """
        try:
            cfg = _config.load_config()
            cfg["enable_thinking"] = bool(enable_thinking)
            cfg["enable_tools"] = bool(enable_tools)
            _config.save_config(cfg)
            # 立即更新 self，下次 start_real_chat 生效
            self._enable_thinking = bool(enable_thinking)
            self._enable_tools = bool(enable_tools)
            return True
        except Exception as e:
            print(f"[chat_bridge] set_preferences 失败: {e}")
            return False

    @pyqtSlot(result='QVariantMap')
    def get_preferences(self):
        """返回当前全局偏好（QML 端读后渲染设置对话框的勾选状态）。"""
        return {
            "enable_thinking": getattr(self, "_enable_thinking", False),
            "enable_tools": getattr(self, "_enable_tools", True),
        }

    @pyqtSlot(str, result=bool)
    def set_expert(self, expert_id: str) -> bool:
        """Day 19 (C-NEW-3 修复): 切到指定专家。成功返回 True。

        QML 端下拉框选专家时调此 slot；/dev 前缀路由也会调。
        """
        experts = self._load_experts()
        if expert_id in experts:
            self._current_expert_id = expert_id
            return True
        return False

    @pyqtSlot(result='QVariantList')
    def list_experts(self):
        """Day 19 (C-NEW-3 修复): 返回所有专家的元信息（QML 端下拉框用）。

        格式: [{id, name, description, current}, ...]
        """
        experts = self._load_experts()
        out = []
        cur = getattr(self, "_current_expert_id", "general")
        for eid, e in experts.items():
            out.append({
                "id": eid,
                "name": e.get("name", eid),
                "description": e.get("description", ""),
                "current": eid == cur,
            })
        return out

    @pyqtSlot(str, result='QVariantMap')
    def get_file_size(self, file_path: str):
        """Day 19 (H-NEW-8): 查文件大小（字节）。

        QML 端 DropArea 拖入文件时调本 slot 判断是否超过 50MB 上限。
        超过直接拒绝 + 显示提示气泡，避免无意义的大文件（ISO / 视频 / 压缩包）
        进 read_text_file / image 附件把 GUI 卡死。

        返回结构: {"ok": bool, "size": int, "error": str}
        - ok=True:  size 是文件字节数
        - ok=False: error 是失败原因（不存在 / 无权限 / 路径为空）
        """
        out = {"ok": False, "size": 0, "error": ""}
        if not file_path:
            out["error"] = "路径为空"
            return out
        # Day 19 (H-NEW-8): 路径字符串过滤（与 read_text_file 一致；
        # Day 19.1 修订：不再拒绝绝对路径，GUI 拖入永远绝对）
        if not is_safe_to_read(file_path):
            out["error"] = "路径字符串不合法（空或含控制字符）"
            return out
        try:
            out["size"] = os.path.getsize(file_path)
            out["ok"] = True
        except FileNotFoundError:
            out["error"] = "文件不存在"
        except PermissionError:
            out["error"] = "无权限访问"
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    @pyqtSlot(result='QVariantList')
    def list_readable_ext(self):
        """Day 19 (M-NEW-4): 暴露 _READABLE_EXT 白名单给 QML。

        之前 QML 端 DropArea.onDropped 自己维护了一份 textExts 数组，与
        Python _READABLE_EXT 重复；任何一处扩展白名单都得手动同步两边，
        容易漂移。现在统一以 Python 为单一来源，QML 通过本 slot 拉取。
        返回纯字符串列表（含点前缀），QML 端用 endsWith(ext) 判断。
        """
        return list(self._READABLE_EXT)

    @pyqtSlot(str, result=str)
    def read_text_file(self, file_path: str) -> str:
        """Day 13: 读文本文件内容（拖入文本文件时附加到输入框用）

        返回格式: "[文件: 路径]\n```lang\n内容\n```"
        - 限制 50KB（更大截断 + 提示）
        - 只读文本类：扩展名白名单 + 绝对路径拒绝（防敏感文件泄露）
        - 出错返回 "[读文件失败: ...]"

        安全（Day 18 修复 C3）：
        - 拒绝绝对路径（仅接受拖入工作区内的相对路径；之前任意路径都接受，
          导致 ~/.ssh/id_rsa、/etc/passwd 等可被读 + 走 LLM 泄露）
        - 扩展名白名单（见 _READABLE_EXT）；二进制/可执行/压缩包直接拒绝
        """
        if not file_path:
            return ""
        # 路径字符串过滤（Day 19.1：不再拒绝绝对路径；GUI 拖入永远绝对）
        if not is_safe_to_read(file_path):
            return "[读文件失败: 路径不合法（空或含控制字符）]"
        # C3 修复：扩展名白名单
        lower = file_path.lower()
        if not any(lower.endswith(ext) for ext in self._READABLE_EXT):
            return ("[读文件失败: 不支持的文件类型（仅文本/代码/配置/文档类）]")
        # 文件大小检查（50KB 限制）
        MAX_SIZE = 50 * 1024
        try:
            size = os.path.getsize(file_path)
        except Exception as e:
            return f"[读文件失败: {e}]"
        if size > MAX_SIZE:
            truncated_msg = f"(文件过大，已截断到 {MAX_SIZE // 1024}KB)"
        else:
            truncated_msg = None
        # 按扩展名选语言标签
        lang = "text"
        for ext, l in ((".py", "python"), (".js", "javascript"), (".ts", "typescript"),
                        (".json", "json"), (".md", "markdown"), (".html", "html"),
                        (".css", "css"), (".sh", "bash"), (".yml", "yaml"),
                        (".yaml", "yaml"), (".xml", "xml"), (".sql", "sql"),
                        (".java", "java"), (".go", "go"), (".rs", "rust"),
                        (".cpp", "cpp"), (".c", "c"), (".h", "c"), (".txt", "text"),
                        (".log", "text"), (".ini", "ini"), (".cfg", "ini"),
                        (".toml", "ini"), (".env", "ini"), (".csv", "text")):
            if lower.endswith(ext):
                lang = l
                break
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_SIZE)
        except Exception as e:
            return f"[读文件失败: {e}]"
        file_name = os.path.basename(file_path)
        out = f"[文件: {file_name}]\n```{lang}\n{content}\n```"
        if truncated_msg:
            out += f"\n{truncated_msg}"
        return out

    # ============ 内部辅助 ============
    def _discover_plugins(self) -> dict:
        """Day 13: 扫描 plugins 目录（plugin_manager.discover_plugins）"""
        try:
            from . import plugin_manager
            plugins, _ = plugin_manager.discover_plugins()
            return plugins
        except Exception as e:
            print(f"[chat_bridge] discover_plugins 失败: {e}")
            return {}

    @pyqtSlot()
    def reload_plugins(self):
        """Day 14: 强制重载 plugins 目录（reload_modules=True 清 sys.modules 缓存）

        Day 18 (H1 修复)：如果当前有 worker 在跑（_worker_count > 0），
        直接 reload 会让 worker 持有的 plugin module 对象引用 unbound，
        工具调用会抛 AttributeError（半写半未写风险）。推迟到所有 worker 退出后
        再 reload（最多等 5 秒，超时仍尝试但 emit 失败信号）。
        Day 18 (M7)：失败时通过 pluginReloadFailed 信号把错误传回 QML，
        不再仅 print 到 stderr（GUI 之前看不到）。
        """
        with self._worker_count_lock:
            if self._worker_count > 0:
                self._reload_pending = True
                self._reload_attempts = getattr(self, "_reload_attempts", 0) + 1
                if self._reload_attempts <= 50:        # 50 × 100ms = 5s 超时
                    QTimer.singleShot(100, self.reload_plugins)
                    return
                # 超时：强制尝试 + emit 失败警告
                self._reload_pending = False
                self.pluginReloadFailed.emit("worker 在 5 秒内未退出，强制重载可能半写")
        self._reload_attempts = 0
        self._do_reload_plugins()

    def _do_reload_plugins(self):
        """实际执行 reload（被 reload_plugins / 延迟重试调用）"""
        try:
            from . import plugin_manager
            plugins, _ = plugin_manager.discover_plugins(reload_modules=True)
            self.pluginReloaded.emit(len(plugins))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[chat_bridge] 插件热更新失败: {err}")
            self.pluginReloadFailed.emit(err)

    def _enabled_plugin_names(self) -> list:
        """Day 13: 列出已启用插件名（带 TOOLS 字段的）

        Day 19 (C-NEW-2 修复)：从 cfg 持久化的 enabled_plugins 字段读，
        与 chat_window._load_plugins 一致 —— 之前是「所有 TOOLS 插件视为启用」，
        QtQuick 路径下用户在「插件管理」里禁用某个插件完全无效。
        """
        try:
            from . import config as _cfg
            from . import plugin_manager
            # 读持久化的启用列表（load_plugin_state 已在 user 排除不存在的）
            saved = _cfg.load_plugin_state()
            plugins, _ = plugin_manager.discover_plugins()
            # 取交集：磁盘上存在 + 标记启用 + 实际有 TOOLS
            return [n for n in saved
                    if n in plugins and hasattr(plugins[n], "TOOLS") and plugins[n].TOOLS]
        except Exception:
            return []

    @staticmethod
    def _mk_msg(text: str, ts: str, code: str = "") -> dict:
        """Day 17: messageAdded 的负载（收敛为单个 QVariantMap，规避 QTBUG-94360）

        QML 端用 `m.code.length > 0` 推导 hasCode，因此不再单独传该字段。
        """
        return {"text": text or "", "ts": ts or "", "code": code or ""}

    def _send_user_bubble(self, text: str):
        ts = datetime.now().strftime("%H:%M")
        self.messageAdded.emit("user", self._mk_msg(text, ts))
        # Day 10: 用户消息立即写到 SQLite（防止崩溃丢失）
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


    # ============ Day 3-5: 真实流式（用 fake OpenAI client 跑真 WorkerThread）============
    def start_real_chat(self, user_text: str, use_fake: bool = False, user_content=None,
                        system_prompt: str = ""):
        """Day 9: 启动一个真 WorkerThread。client 优先从 config 读取：
        - 能读到 api_key/base_url → 用真 LLM则实际发 HTTP 请求。会耗 token。
        - 读不到 / 错误 → 降级到 fake client（验证信号链路用）。

        读取逻辑：
        1. _config.get_current_model() → {api_key, base_url, model_id, proxy, ...}
        2. api_key 非空 → 生成真 client（会发出去）
        3. 否则 → fake

        Day 19 (C-NEW-1 / M-NEW-5 修复)：
        - WorkerThread 收到的 model_id 必须是当前激活模型的真实值，不再
          硬编码 "mock-qwen"
        - enable_thinking / enable_tools 从 self._enable_thinking / tools
          读（这些是用户在「偏好设置」里设的全局偏好；启动时从 cfg 顶层读）

        Day 19 (C-NEW-3 修复)：
        - system_prompt 是 send_message 构造的（专家 + 插件 skill + 工具引导），
          在这里注入到 messages 第一条。空字符串则不注入。
        """
        client = None
        model_name = "未选择"
        # 默认值：未配置模型时回退
        real_model_id = "mock-qwen"
        # Day 19: 思考/工具开关来自全局偏好（用户在「偏好设置」设的）
        # 不从 model dict 读（避免与「按模型设置」混淆）
        real_enable_thinking = getattr(self, "_enable_thinking", False)
        real_enable_tools = getattr(self, "_enable_tools", True)
        # Day 9+: use_fake=True 时跳过真 LLM（单元测试用，不烧 token）
        if use_fake:
            client = _make_fake_openai_client(user_text)
            self._last_model_name = "(fake)"
        else:
            try:
                current_model = _config.get_current_model()
                if current_model and current_model.get("api_key"):
                    model_name = current_model.get("name") or current_model.get("model_id", "?")
                    real_model_id = current_model.get("model_id", "mock-qwen")
                    client = _config.make_openai_client(
                        current_model.get("api_key", ""),
                        current_model.get("base_url", ""),
                        current_model.get("proxy", ""),
                    )
                    self._last_model_name = model_name
            except Exception as e:
                print(f"[chat_bridge] 读取 config 失败: {e}，降级到 fake client")

        if client is None:
            client = _make_fake_openai_client(user_text)
            self._last_model_name = "(fake)"

        # 注意：use_fake=True 分支已经在上面设过 _last_model_name，
        # 真 LLM 分支也在 try 块设过，这里只覆盖"真解析失败 fallback 到 fake"的分支

        # Day 19: 用真实 model_id / 来自全局偏好的 enable_thinking / enable_tools
        # max_rounds=3 是默认；自主模式可在偏好设置里调大（chat_window 路径会读）
        self._worker = WorkerThread(
            client=client,
            model_id=real_model_id,
            enable_thinking=real_enable_thinking,
            enable_tools=real_enable_tools,
            # Day 19 (C-NEW-3 修复): system_prompt（来自专家/插件/工具引导）
            # 插在 messages 第一条。chat_window 路径在 on_send_message 也会插；
            # QtQuick 路径之前完全没插。
            messages=(
                [{"role": "system", "content": system_prompt}] +
                [{"role": "user", "content": (user_content if user_content is not None else user_text)}]
                if system_prompt else
                [{"role": "user", "content": (user_content if user_content is not None else user_text)}]
            ),
            plugins=self._discover_plugins(),
            enabled_plugins=self._enabled_plugin_names(),
            max_rounds=3,                 # 最多 3 轮工具调用循环
            workspace=None,
        )
        # 桥接 worker 信号 → bridge 信号（再由 bridge 触发 QML）
        self._worker.chunk_received.connect(self._on_worker_chunk)
        self._worker.response_complete.connect(self._on_worker_complete)
        self._worker.error_occurred.connect(self._on_worker_error)
        # Day 11: 工具调用桥接
        self._worker.tool_call_start.connect(self._on_worker_tool_call_start)
        self._worker.tool_call_result.connect(self._on_worker_tool_call_result)
        # 线程 finished 也清 busy
        self._worker.finished.connect(self._on_worker_finished)
        # Day 18 (H2 修复)：WorkerThread 必须 deleteLater（每发一条消息会
        # new 一个新 QThread，不释放会泄漏）
        self._worker.finished.connect(lambda wt=self._worker: wt.deleteLater())
        # 启动
        self._set_busy(True)
        # 先发一个空 ai 气泡占位（流式会填进去）
        ts = datetime.now().strftime("%H:%M")
        self._last_who = "ai"
        self.messageAdded.emit("ai", self._mk_msg("", ts))
        # Day 18 (H1)：启动 worker 时 +1 计数（reload_plugins 据此判断能否重载）
        with self._worker_count_lock:
            self._worker_count += 1
        self._worker.start()

    def _on_worker_chunk(self, content: str, is_thinking: bool):
        """WorkerThread.chunk_received → QML.appendToLast

        Day 19 (H-NEW-5 修复): _stream_buffers[who] 累积超 MAX_STREAM_BUFFER
        时裁掉前段，保留最后 1MB（200k 中文字远超上下文需求）。防止
        恶意/失控 LLM 服务端无限流式响应把进程 OOM 死。
        """
        who = "thinking" if is_thinking else "ai"
        if self._last_who != who:
            # 切段（思考 → 回答）：新建气泡
            ts = datetime.now().strftime("%H:%M")
            self.messageAdded.emit(who, self._mk_msg("", ts))
            self._last_who = who
        # Day 6: 累积 buffer（流式期间用 PlainText 显示，finalize 时用 Markdown 替换）
        buf = self._stream_buffers.get(who, "") + content
        if len(buf) > self.MAX_STREAM_BUFFER:
            # 裁前段保留后 1MB
            buf = buf[-self.MAX_STREAM_BUFFER:]
        self._stream_buffers[who] = buf
        self.appendToLast.emit(who, content)

    def _on_worker_complete(self, full: str):
        """WorkerThread.response_complete → finalizeLast + 渲染 Markdown → messageReplaced"""
        if self._last_who:
            self.finalizeLast.emit(self._last_who)
            # Day 6: 流式结束后用 markdown_to_html 渲染整段 → 替换气泡为富文本
            self._flush_stream_buffer(self._last_who)
        self._last_who = None
        # Day 14 候选: 提前清 busy（QThread.finished 不可靠时兜底）
        self._set_busy(False)

    def _flush_stream_buffer(self, who: str):
        """Day 6: 取 buffer → markdown_to_html → emit messageReplaced 让 QML 替换
        Day 10: 同时把 assistant 消息写到 SQLite（response_complete / stop / error 三种收尾都走这里）
        """
        buf = self._stream_buffers.pop(who, "")
        if not buf:
            return
        # Day 10: assistant 消息保存到 SQLite（仅当 who == "ai"，避免错误气泡也保存）
        if who == "ai":
            self._append_history("assistant", buf)
        # A1: 通过 chat_render 抽象层（QtQuick 走 RichText）
        rendered = chat_render.render_text_final(who, buf, self._theme)
        self.messageReplaced.emit(who, rendered)

    def _on_worker_error(self, msg: str):
        """WorkerThread.error_occurred → QML.appendError"""
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        self.appendError.emit("error", f"{msg} ({ts})")
        # 错误时收尾（flush buffer + finalize + 解 busy）
        if self._last_who:
            self._flush_stream_buffer(self._last_who)
            self.finalizeLast.emit(self._last_who)
            self._last_who = None

    def _on_worker_finished(self):
        """QThread.finished 兜底：万一 signal 漏了，确保清 busy + 释放 QThread"""
        self._set_busy(False)
        # Day 15: 释放 WorkerThread（避免每发一条消息泄漏一个 QThread）
        if self._worker is not None:
            w = self._worker
            self._worker = None
            # deleteLater 在事件循环里 GC，比直接 delete 安全
            w.deleteLater()
        # Day 18 (H1)：worker 退出时计数 -1；若之前 reload 因计数 > 0 被推迟，
        # 现在重新尝试一次
        with self._worker_count_lock:
            self._worker_count = max(0, self._worker_count - 1)
            pending = self._worker_count == 0 and getattr(self, "_reload_pending", False)
            if pending:
                self._reload_pending = False
        if pending:
            QTimer.singleShot(0, self._do_reload_plugins)

    # ============ Day 11: 工具调用信号桥接 ============
    def _on_worker_tool_call_start(self, name: str, args_str: str):
        """WorkerThread.tool_call_start → QML 显示工具调用气泡

        实现方式：往 messageModel append 一个 who="tool_call" 的条目，
        QML delegate 看到这种类型会渲染成折叠卡片样式。
        同时 emit toolCallStarted 给 QML 端可能要做的特殊处理。
        """
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        # 解析 args（如果有的话）
        args_display = args_str
        try:
            import json as _json
            args_obj = _json.loads(args_str) if args_str else {}
            args_display = _json.dumps(args_obj, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # 推到 messageModel（who="tool_call"，text=name，code=args）
        self.messageAdded.emit("tool_call", self._mk_msg(name, ts, args_display))
        self.toolCallStarted.emit(name, args_str)

    def _on_worker_tool_call_result(self, name: str, args_str: str, result: str):
        """WorkerThread.tool_call_result → QML 显示工具结果气泡

        推到 messageModel（who="tool_result"，text=name，code=result_display）
        同时 emit toolCallResult(name, {args, result}) 给 QML 端可能要做的特殊处理。
        """
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        # 截断过长结果（避免气泡爆长）
        result_display = result if len(result) <= 800 else (result[:800] + "\n... (已截断)")
        self.messageAdded.emit("tool_result", self._mk_msg(name, ts, result_display))
        # Day 19.1 修订：toolCallResult 改为 (name, QVariantMap) ≤2 参数，
        # 防 QTBUG-94360（QML Connections 监听 ≥3 参数信号栈越界崩溃）。
        self.toolCallResult.emit(name, {"args": args_str, "result": result})

    # ============ Day 1-2 兼容：旧 mock 路径（保留但默认不用）============
    def _simulate_ai_reply(self, prompt: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        self._last_who = "ai"
        self.messageAdded.emit("ai", self._mk_msg("", ts))
        replies = [
            f"我理解你问的是：{prompt[:30]}...",
            "这是 mock 路径（Day 1-2 用），send_message 已默认改走真实路径。",
            "如果你看到这条，说明 mock 桥接工作正常 ✓"
        ]
        self.appendToLast.emit("ai", replies[0])
        QTimer.singleShot(600, lambda: self._push_chunk(replies[1]))
        QTimer.singleShot(1200, lambda: self._push_chunk(replies[2]))
        QTimer.singleShot(1500, self._finish_simulate)

    def _push_chunk(self, content: str):
        self.appendToLast.emit("ai", "\n\n" + content)

    def _finish_simulate(self):
        self.finalizeLast.emit("ai")
        self._last_who = None

    # ============ Day 20.1: 设置/模型/自动化菜单入口 ============
    # QtQuick 路径下没有 PyQt5 的菜单栏，Day 20 工程师漏补「设置/模型/自动化」
    # 三组菜单。PyQt5 对话框（settings_dialog / automation_dialogs）在
    # 已有 QApplication 进程里也能弹，所以 QML 调这些 slot 时直接 spawn
    # PyQt5 子窗口 —— 视觉风格略不一致但功能完整。
    #
    # Day 20.2: PyQt5 对话框的 show_* 函数是为 ChatWindow(QWidget) 写的，要
    # 求 parent 是 QWidget 且 host 暴露十几个公开字段/方法。ChatBridge 是
    # QObject —— 硬传 self 会 QDialog(ChatBridge) TypeError。用 _DialogHost
    # 包一层，host 缓存到 self._dialog_host 上重复复用（多组对话框共享）。
    @pyqtSlot()
    def show_settings_dialog(self):
        """Day 20.1: QtQuick 菜单栏「设置 > 模型设置」入口"""
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            show_settings(host)
        except Exception as e:
            print(f"[chat_bridge] show_settings 失败: {e}")
            self.toast.emit(f"打开设置失败: {e}")

    @pyqtSlot()
    def show_plugin_manager_dialog(self):
        """Day 20.1: QtQuick 菜单栏「设置 > 插件管理」入口"""
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            show_plugin_manager(host)
        except Exception as e:
            print(f"[chat_bridge] show_plugin_manager 失败: {e}")
            self.toast.emit(f"打开插件管理失败: {e}")

    @pyqtSlot()
    def show_model_manager_dialog(self):
        """Day 20.1: QtQuick 菜单栏「模型 > 模型管理」入口"""
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            show_model_manager(host)
        except Exception as e:
            print(f"[chat_bridge] show_model_manager 失败: {e}")
            self.toast.emit(f"打开模型管理失败: {e}")

    def _get_dialog_host(self):
        """获取（或懒创建）_DialogHost。host 是 QWidget，对话框可当 parent；
        对话框访问的字段 / 方法都在 host 上代理到 bridge 或 cfg。"""
        from PyQt5.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
        host = getattr(self, "_dialog_host", None)
        if host is None:
            host = _DialogHost(self)
            self._dialog_host = host
        return host

    @pyqtSlot()
    def show_automation_manager_dialog(self):
        """Day 20.1/20.3: QtQuick 菜单栏「工具 > 任务管理」入口

        Day 20.1 时 automation_dialogs.py 是空模块，slot 只能 toast 占位。
        Day 20.3: automation_dialogs 补了 ``show_automation_manager(host)`` 工厂
        （跟 settings_dialog.show_* 同一套模式），host 走 _DialogHost，会按
        standalone 模式懒构造 Scheduler（与 chat_window 的 chat_scheduler 行为
        对齐：30s 定时 + 立即 check_due）。
        """
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            from .automation_dialogs import show_automation_manager
            show_automation_manager(host)
        except Exception as e:
            print(f"[chat_bridge] show_automation_manager 失败: {e}")
            self.toast.emit(f"打开自动化管理失败: {e}")

    @pyqtSlot()
    def run_automation_check_due(self):
        """Day 20.1/20.3: 菜单栏「工具 > 立即检查」入口

        复用 _DialogHost.scheduler 懒构造（与 show_automation_manager_dialog
        共享同一个 Scheduler 实例）。如果用户在打开「任务管理」之前就点了
        「立即检查」，host.scheduler 会在这里第一次触发懒构造。
        """
        try:
            host = self._get_dialog_host()
            if host is None or host.scheduler is None:
                self.toast.emit("调度器未启动")
                return
            host.scheduler.check_due()
            self.toast.emit("已触发到期任务检查")
        except Exception as e:
            print(f"[chat_bridge] run_automation_check_due 失败: {e}")
            self.toast.emit(f"检查任务失败: {e}")


# ============ 模块级：fake OpenAI client ============
def _make_fake_openai_client(user_text: str):
    """构造一个能跑通 WorkerThread.run() 的假 OpenAI client。

    模仿 openai.OpenAI 的 chat.completions.create(..., stream=True) 返回迭代器，
    每个 chunk 有 choices[0].delta.content / .reasoning_content。
    """
    def make_chunk(text: str = "", reasoning: str = "", is_last: bool = False):
        delta = SimpleNamespace(content=text or None,
                               reasoning_content=reasoning or None,
                               tool_calls=None)
        choice = SimpleNamespace(index=0, delta=delta, finish_reason="stop" if is_last else None)
        return SimpleNamespace(id="mock", choices=[choice], model="mock-qwen", object="chat.completion.chunk")

    # Day 6: 故意包含 markdown 语法（加粗 / 行内代码 / 围栏代码 / 列表 / 标题）
    # 流式阶段 PlainText 显示，finalize 时 messageReplaced 触发 markdown 渲染版
    text = (
        f"你说的是 **" + user_text + "**。\n\n"
        "下面给你看个 Python `quicksort` 的实现：\n\n"
        "```python\n"
        "def quicksort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    pivot = arr[0]\n"
        "    left = [x for x in arr[1:] if x < pivot]\n"
        "    right = [x for x in arr[1:] if x >= pivot]\n"
        "    return quicksort(left) + [pivot] + quicksort(right)\n"
        "```\n\n"
        "**关键点**：\n"
        "- 时间复杂度 O(n log n)\n"
        "- 空间复杂度 O(n)\n"
        "- 不稳定排序\n"
    )
    chunk_size = 15
    pieces = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    chunks = [make_chunk(p) for p in pieces] + [make_chunk(is_last=True)]

    class FakeCompletions:
        def create(self, **kwargs):
            def gen():
                # 不在 fake 里 sleep（会引起 offscreen QML segfault）。
                # 流式延迟在 QML 端用 QTimer 间隔 emit。
                for c in chunks:
                    yield c
            return gen()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    return FakeClient()
