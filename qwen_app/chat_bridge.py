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
from ._bridge_plugin import PluginMixin  # 拆分 Step 1: 插件管理方法（信号声明仍在本类体内）
from ._bridge_session import SessionMixin  # 拆分 Step 2: 会话管理方法
from ._bridge_bubble import BubbleMixin  # 拆分 Step 3: 气泡操作方法
from ._bridge_model import ModelMixin  # 拆分 Step 4: 模型/偏好/专家/文件读取方法
from ._bridge_send import SendMixin  # 拆分 Step 5: 发送/系统提示词/历史方法
from ._bridge_stream import StreamMixin, LegacyMockMixin  # 拆分 Step 6: 流式/legacy mock 方法


class ChatBridge(PluginMixin, SessionMixin, BubbleMixin, ModelMixin, SendMixin,
                 StreamMixin, LegacyMockMixin, QObject):

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
        Day 20.4 强化：scheduler 懒构造可能因 make_openai_client / discover_plugins
        / Scheduler 构造失败走退化路径；dialog 内部所有调用都包了 try；
        本 slot 仅在 import / dialog 构造阶段抛异常时才会被 except 兜底。
        """
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            from .automation_dialogs import show_automation_manager
            show_automation_manager(host)
            # 成功提示（不阻塞 exec_，但用户能看见）
            # 注意：show_automation_manager 阻塞到 dialog 关闭才返回，
            # 这条 toast 在 dialog 关掉后才发
            n = len(getattr(host.scheduler, "automations", []) or [])
            self.toast.emit(f"任务管理已关闭（{n} 个任务）")
        except Exception as e:
            print(f"[chat_bridge] show_automation_manager 失败: {e}")
            import traceback
            traceback.print_exc()
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

