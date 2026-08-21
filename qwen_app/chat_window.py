"""ChatWindow — PyQt5 主窗口"""
import os
import sys
import json
import uuid
from datetime import datetime
from typing import Optional

from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QMenu,
                             QStatusBar, QMessageBox, QFileDialog,
                             QLabel,
                             QListWidget, QFrame,
                             QApplication, QComboBox)
from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtGui import QTextCharFormat, QColor, QTextCursor, QFont, QKeySequence
from PyQt5.QtWidgets import QAction

from .worker import WorkerThread
from .config import load_config, save_config, load_conversations, save_conversations, load_plugin_state, save_plugin_state, make_openai_client
from .sanitizer import sanitize
from .tools import DEFAULT_CONFIG
from .plugin_manager import discover_plugins, get_enabled_tools, dispatch_tool, compare_versions, get_plugin_meta, get_system_prompts
from .compressor import ConversationCompressor
from .scheduler import Scheduler, describe_schedule, is_due
from .expert_router import (load_experts, match_expert, resolve_settings,
                           build_system_prompt)
from .automation_dialogs import (AutomationManagerDialog, AutomationEditDialog,
                                 LogViewDialog)
from .theme import (apply_theme_to_window, build_bubble, get_palette)
from .session import (get_current_conv, new_conversation, on_conv_selected,
                      refresh_conv_list, load_current_conv, save_current_to_conv,
                      load_convs, save_convs, select_conv_by_id, show_conv_menu,
                      rename_conversation, delete_conversation)
from .settings_dialog import (show_settings, show_plugin_manager,
                              reload_plugins)


class ChatWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.worker_thread = None
        self.enable_context = True
        self.current_tag = None
        self.conversation_history = []
        self._raw_buffer = ""
        self._block_start_pos = None
        self._cur_sender = ""
        self._cur_tag = "ai"
        self._cur_time = ""
        self._compressor: Optional[ConversationCompressor] = None
        self._load_settings()
        self._load_plugins()
        self.experts = load_experts()
        self.current_expert_id = self._load_current_expert()
        self.setup_client()
        load_convs(self)
        # ── 自动化调度器：常驻后台，每 30s 检查到期任务 ──
        self.scheduler = Scheduler(
            parent=self,
            plugins=self.plugins,
            enabled_plugins=self.enabled_plugins,
            max_rounds=self.max_agent_rounds,
        )
        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self.scheduler.check_due)
        self._sched_timer.start(30000)  # 30 秒
        self.setup_ui()

    # ═══════════════════════════════════════════════
    #  界面搭建
    # ═══════════════════════════════════════════════

    def setup_ui(self):
        self.setWindowTitle("AI 对话助手")
        self.resize(1000, 700)
        self.setMinimumSize(800, 500)

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 左侧边栏 ──
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        # 侧边栏（及全部界面）样式统一由 apply_theme() 注入
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)

        title_bar = QHBoxLayout()
        title_label = QLabel("对话历史")
        title_label.setObjectName("sidebarTitle")
        title_bar.addWidget(title_label)
        title_bar.addStretch()
        btn_new = QPushButton("＋ 新建")
        btn_new.setObjectName("btnNewChat")
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.clicked.connect(lambda: new_conversation(self))
        title_bar.addWidget(btn_new)
        sidebar_layout.addLayout(title_bar)

        self.conv_list = QListWidget()
        self.conv_list.setObjectName("convList")
        self.conv_list.itemClicked.connect(lambda item: on_conv_selected(self, item))
        # 强制关闭横向滚动条：标题已 elide 截断，绝不出现左右滚动
        self.conv_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 视口宽度变化(首次 show/窗口 resize/滚动条出现)时重建行宽，保证 ⋯ 按钮完整可见
        self.conv_list.installEventFilter(self)
        sidebar_layout.addWidget(self.conv_list)

        bottom_bar = QHBoxLayout()
        btn_theme = QPushButton("🌓")
        btn_theme.setFlat(True)
        btn_theme.setCursor(Qt.PointingHandCursor)
        btn_theme.setToolTip("切换浅色 / 深色主题")
        btn_theme.clicked.connect(self._toggle_theme)
        bottom_bar.addWidget(btn_theme)
        btn_settings = QPushButton("⚙ 设置")
        btn_settings.setFlat(True)
        btn_settings.setCursor(Qt.PointingHandCursor)
        btn_settings.clicked.connect(lambda: show_settings(self))
        bottom_bar.addWidget(btn_settings)
        bottom_bar.addStretch()
        sidebar_layout.addLayout(bottom_bar)

        main_layout.addWidget(sidebar)

        # ── 右侧聊天区 ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # ── 专家选择条 ──
        self._setup_expert_bar(right_layout)

        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chatDisplay")
        self.chat_display.setFocusPolicy(Qt.NoFocus)
        self.chat_display.installEventFilter(self)
        # 在 QApplication 级别安装事件过滤器，确保无论焦点在哪个 widget
        # 都能抢在 QLineEdit 等控件之前拦截 Ctrl+C 复制 chat_display 选中文本
        QApplication.instance().installEventFilter(self)
        right_layout.addWidget(self.chat_display)

        input_frame = QFrame()
        input_frame.setObjectName("inputFrame")
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(16, 12, 16, 12)
        input_layout.setSpacing(10)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入消息，回车发送...")
        self.input_field.returnPressed.connect(self.on_send_message)
        input_layout.addWidget(self.input_field)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("btnSend")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.clicked.connect(self.on_send_message)
        input_layout.addWidget(self.send_button)

        self.stop_button = QPushButton("■ 停止")
        self.stop_button.setObjectName("btnStop")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.clicked.connect(self.on_stop_response)
        self.stop_button.hide()
        input_layout.addWidget(self.stop_button)

        right_layout.addWidget(input_frame)
        main_layout.addWidget(right_panel, stretch=1)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #666; padding: 0 8px;")
        self.status_bar.addPermanentWidget(self.status_label)
        self.update_status()

        self.create_menu()
        self._setup_copy_handler()

        # 注入主题（浅色 / 深色）样式；聊天内容由下方按会话加载渲染
        self.apply_theme(rerender_chat=False)

        if not self.conversations:
            new_conversation(self)
        else:
            refresh_conv_list(self)
            load_current_conv(self)

    # ═══════════════════════════════════════════════
    #  客户端 & 设置
    # ═══════════════════════════════════════════════

    def _load_plugins(self):
        self.plugins, self.plugin_infos = discover_plugins()
        self.enabled_plugins = load_plugin_state()
        # 过滤掉不存在的插件
        self.enabled_plugins = [p for p in self.enabled_plugins if p in self.plugins]

    def setup_client(self):
        try:
            self.client = make_openai_client(
                self.api_key, self.base_url, self.proxy)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"客户端初始化失败: {e}")
            sys.exit(1)

    def _load_settings(self):
        cfg = load_config()
        self.model_id = cfg.get("model_id", DEFAULT_CONFIG["model_id"])
        self.api_key = cfg.get("api_key", DEFAULT_CONFIG["api_key"])
        self.base_url = cfg.get("base_url", DEFAULT_CONFIG["base_url"])
        self.enable_thinking = cfg.get("enable_thinking", DEFAULT_CONFIG["enable_thinking"])
        self.enable_tools = cfg.get("enable_tools", DEFAULT_CONFIG["enable_tools"])
        self.workspace_root = cfg.get("workspace_root", DEFAULT_CONFIG["workspace_root"])
        self.agent_mode = cfg.get("agent_mode", DEFAULT_CONFIG["agent_mode"])
        self.max_agent_rounds = cfg.get("max_agent_rounds", DEFAULT_CONFIG["max_agent_rounds"])
        self.proxy = cfg.get("proxy", DEFAULT_CONFIG.get("proxy", ""))
        self.theme = cfg.get("ui_theme", "light")

    def _save_settings(self):
        save_config({
            "model_id": self.model_id,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "enable_thinking": self.enable_thinking,
            "enable_tools": self.enable_tools,
            "workspace_root": self.workspace_root,
            "agent_mode": self.agent_mode,
            "max_agent_rounds": self.max_agent_rounds,
            "proxy": self.proxy,
            "ui_theme": self.theme,
        })

    def _pick_workspace(self, line_edit):
        """打开目录选择对话框，设置工作区根目录"""
        start = line_edit.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择工作区根目录", start)
        if path:
            line_edit.setText(path)

    def create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("新建对话", lambda: new_conversation(self), "Ctrl+N")
        file_menu.addAction("导出对话", self.export_conversation, "Ctrl+S")
        file_menu.addAction("导入对话", self.import_conversation, "Ctrl+O")
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close, "Alt+F4")

        edit_menu = menubar.addMenu("编辑")
        edit_menu.addAction("清空对话内容", self.clear_conversation)
        edit_menu.addAction("压缩对话历史", self.compress_conversation, "Ctrl+Shift+C")
        edit_menu.addAction("切换上下文记忆", self.toggle_context, "Ctrl+T")

        settings_menu = menubar.addMenu("设置")
        settings_menu.addAction("模型设置...", lambda: show_settings(self))
        settings_menu.addAction("插件管理...", lambda: show_plugin_manager(self))

        # ── 自动化菜单（定时任务 + 按次记录结果）──
        auto_menu = menubar.addMenu("自动化")
        auto_menu.addAction("任务管理...", self.show_automation_manager)
        auto_menu.addAction("立即检查并执行到期任务",
                             lambda: self.scheduler.check_due())

        help_menu = menubar.addMenu("帮助")
        help_menu.addAction("关于", self.show_about)

    # ═══════════════════════════════════════════════
    #  复制快捷键绑定
    # ═══════════════════════════════════════════════

    def _setup_copy_handler(self):
        """方案C：显式绑定复制 QAction，确保 Ctrl+C 在聊天区域可用"""
        self.copy_action = QAction("复制", self)
        self.copy_action.setShortcuts([QKeySequence.Copy, QKeySequence("Ctrl+Insert")])
        self.copy_action.triggered.connect(self._handle_copy)
        self.addAction(self.copy_action)
        self.chat_display.addAction(self.copy_action)

    def _handle_copy(self):
        """处理复制操作：将选中文本复制到剪贴板"""
        cursor = self.chat_display.textCursor()
        if cursor.hasSelection():
            selected = cursor.selectedText()
            clipboard = QApplication.clipboard()
            clipboard.setText(selected)
            self.status_label.setText("✓ 已复制选中内容")
            # 1.5秒后恢复原状态文字
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(1500, self.update_status)

    # ═══════════════════════════════════════════════
    #  事件过滤
    # ═══════════════════════════════════════════════

    def eventFilter(self, obj, event):
        """事件过滤器（唯一入口）：
        1) conv_list / viewport 宽度变化 → 延迟重建列表项，保证 ⋯ 按钮完整可见；
        2) QApplication 级别拦截 Ctrl+C 优先复制 chat_display 文本；
        3) chat_display 级别拦截文本输入事件

        注意：本方法在 setup_ui 构造过程中即会被调用（conv_list 的过滤器在
        chat_display 创建之前安装，addWidget 会同步派发事件）。因此对
        chat_display / input_field / status_label 的访问必须用防御性 getattr，
        任何异常穿越 C++ 回调栈都会导致进程硬崩（Windows 表现为段错误）。
        """
        etype = event.type()

        # ── conv_list / viewport Resize：延迟重建 ──
        # 关键时序：conv_list 先收到 Resize，此刻 viewport 还是旧宽度；
        # 必须延迟到布局稳定后（singleShot 0）再读真实视口宽，否则
        # vw == 上次记录值 会跳过重建，item 宽度被锁死在构造期假值 636px，
        # ⋯ 按钮被推到视口之外 → 用户看不到、点不到。
        conv_list = getattr(self, "conv_list", None)
        if conv_list is not None and etype == QEvent.Resize and (
                obj is conv_list or obj is conv_list.viewport()):
            QTimer.singleShot(0, self._maybe_refresh_conv_list)

        chat_display = getattr(self, "chat_display", None)

        # ── QApplication 级别：全局拦截 Ctrl+C ──
        # 因为 chat_display 设置了 NoFocus，键盘事件不会发给它。
        # 必须在 QApplication 级别拦截，优先级高于 QLineEdit 等控件的内部处理。
        if chat_display is not None and obj is not chat_display and etype == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if (key == Qt.Key_C and modifiers == Qt.ControlModifier
                    and obj is not getattr(self, "input_field", None)):
                # 优先检查 chat_display 是否有选中文本
                cursor = chat_display.textCursor()
                if cursor.hasSelection():
                    clipboard = QApplication.clipboard()
                    clipboard.setText(cursor.selectedText())
                    status_label = getattr(self, "status_label", None)
                    if status_label is not None:
                        status_label.setText("已复制选中内容")
                        QTimer.singleShot(1500, self.update_status)
                    return True  # 消费事件，阻止继续传播
                # chat_display 无选中文本时，不拦截，让其他控件正常处理复制

        # ── chat_display 级别：拦截文本编辑事件 ──
        if chat_display is None or obj is not chat_display:
            return False

        # 放行 ShortcutOverride 事件，让 Qt 快捷键系统正常工作
        if etype == QEvent.ShortcutOverride:
            return False

        if etype == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            # 放行所有 Ctrl/Alt 修饰键组合（Ctrl+C 复制、Ctrl+A 全选等）
            if modifiers & (Qt.ControlModifier | Qt.AltModifier):
                return False
            # 放行导航键
            nav_keys = {Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right,
                        Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End}
            if key in nav_keys:
                return False
            # 拦截其余按键（防止用户在只读区域误输入）
            return True
        if etype in (QEvent.KeyRelease, QEvent.InputMethod, QEvent.InputMethodQuery):
            return True
        return super().eventFilter(obj, event)

    def _maybe_refresh_conv_list(self):
        """布局稳定后读取真实视口宽，与上次记录不一致才重建列表项宽度。

        必须在事件回调之外读取：conv_list 的 Resize 事件早于 viewport，
        回调内读到的 viewport 宽度是陈旧的，会误判为"宽度没变"而跳过重建。
        """
        vw = self.conv_list.viewport().width()
        if vw and vw != getattr(self, "_conv_vp_w", -1):
            refresh_conv_list(self)

    # ═══════════════════════════════════════════════
    #  消息发送
    # ═══════════════════════════════════════════════

    # ═══════════════════════════════════════════════
    #  专家路由
    # ═══════════════════════════════════════════════

    def _load_current_expert(self):
        """从配置读取当前专家，校验存在性，缺省 general"""
        eid = load_config().get("current_expert", "general")
        if eid not in self.experts:
            eid = "general"
        return eid

    def _save_current_expert(self):
        """持久化当前专家选择到 model_config.json"""
        cfg = load_config()
        cfg["current_expert"] = self.current_expert_id
        save_config(cfg)

    def _populate_expert_combo(self):
        """填充专家下拉框，并把当前项同步到已保存的 current_expert_id

        注意：填充期间用 blockSignals 屏蔽信号，否则 addItem 首条会触发
        currentTextChanged -> _on_expert_changed，把 current_expert_id 误写成
        第一项（general）并持久化，导致每次重启都丢失用户已选专家。
        """
        self.expert_combo.blockSignals(True)
        self.expert_combo.clear()
        for eid, e in self.experts.items():
            self.expert_combo.addItem(e.get("name", eid), eid)
        idx = self.expert_combo.findData(self.current_expert_id)
        if idx >= 0:
            self.expert_combo.setCurrentIndex(idx)
        self.expert_combo.blockSignals(False)

    def _on_expert_changed(self, _name):
        """UI 手动切换专家"""
        eid = self.expert_combo.currentData()
        if eid:
            self.current_expert_id = eid
            self._save_current_expert()
            e = self.experts.get(eid, {})
            desc = e.get("description", "")
            if desc:
                self.display_message(
                    "系统", f"已切换专家：{e.get('name', eid)} — {desc}", "system")

    def _sync_expert_selector(self):
        """把 current_expert_id 同步回下拉框（前缀路由后调用）"""
        idx = self.expert_combo.findData(self.current_expert_id)
        if idx >= 0:
            self.expert_combo.setCurrentIndex(idx)

    def _setup_expert_bar(self, right_layout):
        """在聊天区顶部创建专家选择条"""
        bar = QWidget()
        bar.setObjectName("expertBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(8)

        label = QLabel("专家")
        label.setObjectName("expertLabel")
        self.expert_combo = QComboBox()
        self.expert_combo.setObjectName("expertCombo")
        self.expert_combo.setCursor(Qt.PointingHandCursor)
        self._populate_expert_combo()
        self.expert_combo.currentTextChanged.connect(self._on_expert_changed)

        h.addWidget(label)
        h.addWidget(self.expert_combo, 1)
        right_layout.addWidget(bar)

    def _sanitize_messages(self, messages):
        """清理消息序列，确保 user/assistant 交替出现。
        连续 user 消息只保留最后一条（通常是上次请求失败遗留的）。"""
        if not messages:
            return messages
        result = []
        for msg in messages:
            if msg.get("role") == "user" and result and result[-1].get("role") == "user":
                # 连续 user：替换前一条
                result[-1] = msg
            else:
                result.append(msg)
        return result

    def on_send_message(self):
        user_input = self.input_field.text().strip()

        # ── 专家前缀路由：/dev xxx 自动切换并剥离前缀 ──
        matched_id, stripped = match_expert(user_input, self.experts)
        if matched_id:
            self.current_expert_id = matched_id
            self._sync_expert_selector()
            self._save_current_expert()
        user_input = stripped or user_input
        if not user_input:
            self.input_field.clear()
            return

        self.input_field.clear()
        now = datetime.now().strftime("%H:%M")
        self.display_message("您", user_input, "user", now)

        if self.enable_context:
            # 防御：如果最后一条已经是 user（上次请求失败无 assistant 回复），
            # 用新消息替换，避免连续 user 消息导致 API 500 错误
            if self.conversation_history and self.conversation_history[-1].get("role") == "user":
                self.conversation_history[-1] = {"role": "user", "content": user_input, "time": now}
            else:
                self.conversation_history.append({"role": "user", "content": user_input, "time": now})
            # 清理消息序列中任何连续 user 消息（历史数据修复）
            self.conversation_history = self._sanitize_messages(self.conversation_history)
            messages = list(self.conversation_history)
        else:
            messages = [{"role": "user", "content": user_input}]

        # ── 构建 system prompt（专家优先，未指定则回退全局设置）──
        expert = self.experts.get(self.current_expert_id, {})
        ep, use_tools, use_thinking, rounds = resolve_settings(
            expert, self.enabled_plugins, self.enable_tools,
            self.enable_thinking, getattr(self, "agent_mode", False),
            getattr(self, "max_agent_rounds", 5))
        if ep or (expert.get("system_prompt") or "").strip():
            agent_on = getattr(self, "agent_mode", False) and use_tools
            sp = build_system_prompt(expert, self.plugins, ep, use_tools,
                                     agent_mode=agent_on)
            if sp:
                has_system = any(m.get("role") == "system" for m in messages)
                if not has_system:
                    messages.insert(0, {"role": "system", "content": sp})

        if self.worker_thread:
            self.worker_thread.stop()
            self.worker_thread.wait(3000)  # 最多等 3 秒
            if self.worker_thread.isRunning():
                self.worker_thread.terminate()  # 超时强杀
                self.worker_thread.wait(1000)
            self.worker_thread = None

        self.current_tag = None

        self.send_button.setEnabled(False)
        self.stop_button.show()
        self.input_field.setEnabled(False)

        self.worker_thread = WorkerThread(self.client, self.model_id,
                                          use_thinking, use_tools,
                                          messages,
                                          plugins=self.plugins,
                                          enabled_plugins=ep,
                                          max_rounds=rounds)
        self.worker_thread.chunk_received.connect(self.handle_chunk)
        self.worker_thread.response_complete.connect(self.handle_response_complete)
        self.worker_thread.error_occurred.connect(self.handle_error)
        self.worker_thread.tool_call_start.connect(self.handle_tool_call_start)
        self.worker_thread.tool_call_result.connect(self.handle_tool_call_result)
        self.worker_thread.finished.connect(self._on_worker_finished)
        self.worker_thread.start()

    def on_stop_response(self):
        if self.worker_thread:
            self.worker_thread.stop()
            self.worker_thread.wait(3000)  # 最多等 3 秒让线程退出
            if self.worker_thread.isRunning():
                self.worker_thread.terminate()
                self.worker_thread.wait(1000)
            self.worker_thread = None
            self.display_message("系统", "已中断回复", "system")
            self._on_worker_finished()

    def _on_worker_finished(self):
        self.send_button.setEnabled(True)
        self.stop_button.hide()
        self.input_field.setEnabled(True)
        self.input_field.setFocus()
        self.update_status()

    # ── 格式化 ──

    def _get_format(self, tag):
        fmt = QTextCharFormat()
        fmt.setFontFamily("Microsoft YaHei")
        fmt.setFontPointSize(11)
        if tag == "user":
            fmt.setForeground(QColor("#0066CC"))
            fmt.setFontWeight(QFont.Bold)
        elif tag == "ai":
            fmt.setForeground(QColor("#333333"))
            fmt.setFontWeight(QFont.Normal)
        elif tag == "thinking":
            fmt.setForeground(QColor("#999999"))
            fmt.setFontItalic(True)
        elif tag == "tool":
            fmt.setForeground(QColor("#CC6600"))
            fmt.setFontWeight(QFont.Normal)
            fmt.setFontItalic(True)
        elif tag == "error":
            fmt.setForeground(QColor("#CC0000"))
            fmt.setFontWeight(QFont.Bold)
        else:
            fmt.setForeground(QColor("#888888"))
            fmt.setFontItalic(True)
        return fmt

    def _scroll_to_bottom(self):
        vsb = self.chat_display.verticalScrollBar()
        vsb.setValue(vsb.maximum())

    # ── 消息块 ──

    def _start_message_block(self, sender, tag):
        now = datetime.now().strftime("%H:%M")
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.position() > 0:
            cursor.insertHtml("<br>")
        self._block_start_pos = cursor.position()
        self._cur_sender = sender
        self._cur_tag = tag
        self._cur_time = now
        self._raw_buffer = ""

    def _rerender_block(self, tag):
        if self._block_start_pos is None:
            return
        cursor = self.chat_display.textCursor()
        cursor.setPosition(self._block_start_pos)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        clean = sanitize(self._raw_buffer)
        if clean:
            cursor.insertHtml(build_bubble(
                self.theme, self._cur_sender, clean, tag, self._cur_time))
        self._scroll_to_bottom()

    def _append_text(self, text, tag):
        self._raw_buffer += text
        self._rerender_block(tag)

    def handle_chunk(self, content, is_thinking):
        tag = "thinking" if is_thinking else "ai"
        if self.current_tag != tag:
            sender = f"{self.model_id} 思考" if is_thinking else self.model_id
            self._start_message_block(sender, tag)
            self.current_tag = tag
        self._append_text(content, tag)

    def handle_response_complete(self, full_response):
        if hasattr(self, "_raw_buffer") and self._raw_buffer:
            self._rerender_block(self.current_tag or "ai")
        if self.enable_context and full_response:
            self.conversation_history.append({
                "role": "assistant", "content": full_response,
                "time": getattr(self, "_cur_time", datetime.now().strftime("%H:%M")),
            })
        self.current_tag = None
        self._block_start_pos = None
        self._raw_buffer = ""
        save_current_to_conv(self)
        self.update_status()

    def handle_error(self, error_msg):
        self.display_message("系统", f"错误: {error_msg}", "error")
        self.current_tag = None
        # 请求失败时回滚：移除最后一条 user 消息（无对应 assistant 回复），
        # 避免下次发送时连续 user 消息导致 API 500 错误
        if self.enable_context and self.conversation_history:
            if self.conversation_history[-1].get("role") == "user":
                self.conversation_history.pop()

    # ═══════════════════════════════════════════════
    #  主题 & 气泡渲染（QQ 风格）
    # ═══════════════════════════════════════════════



    def apply_theme(self, rerender_chat=True):
        """应用浅色 / 深色主题：委托 theme 模块实现。"""
        apply_theme_to_window(self, rerender_chat)

    def _toggle_theme(self):
        """侧边栏 🌓 按钮：在浅色 / 深色之间切换并持久化。"""
        self.theme = "dark" if self.theme == "light" else "light"
        self._save_settings()
        self.apply_theme(rerender_chat=True)



    def display_message(self, sender, message, tag=None, time_str=None):
        message = sanitize(message)
        if not message:
            return
        if not time_str:
            time_str = datetime.now().strftime("%H:%M")
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.position() > 0:
            cursor.insertHtml("<br>")
        cursor.insertHtml(build_bubble(self.theme, sender, message, tag or "ai", time_str))
        self._scroll_to_bottom()
        self.current_tag = None

    # ── 工具调用显示 ──

    def handle_tool_call_start(self, name, args_str):
        self.current_tag = None
        try:
            args_obj = json.loads(args_str) if args_str else {}
            args_display = json.dumps(args_obj, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            args_display = args_str
        self.display_message("🔧 调用工具", f"{name}\n参数: {args_display}", "tool")

    def handle_tool_call_result(self, name, args_str, result):
        self.current_tag = None
        self.display_message("📊 工具结果", result, "tool")

    # ═══════════════════════════════════════════════
    #  功能操作
    # ═══════════════════════════════════════════════

    def update_status(self):
        status = "开启" if self.enable_context else "关闭"
        tools_status = "开" if self.enable_tools else "关"
        conv = get_current_conv(self)
        if conv:
            # 状态栏标题：压缩空白并截断，避免长标题把状态栏撑臃肿
            conv_title = " ".join(str(conv["title"]).split())
            if len(conv_title) > 18:
                conv_title = conv_title[:18] + "…"
            history = conv.get("history", [])
            user_count = len([m for m in history if m.get("role") == "user"])
            total_count = len(history)
        else:
            conv_title = "无"
            user_count = 0
            total_count = 0
        self.status_label.setText(
            f"💬 {conv_title}  |  上下文: {status}  |  工具: {tools_status}  |  "
            f"对话: {user_count} 轮 ({total_count} 条消息)")

    def toggle_context(self):
        self.enable_context = not self.enable_context
        self.update_status()
        self.display_message("系统", f"上下文记忆已{'开启' if self.enable_context else '关闭'}", "system")

    def compress_conversation(self):
        """压缩当前对话历史：先保存，再用 AI 生成摘要替换旧历史"""
        conv = get_current_conv(self)
        history = conv.get("history", []) if conv else []

        if not history:
            QMessageBox.information(self, "提示", "当前对话为空，无需压缩。")
            return

        if len(history) <= ConversationCompressor.COMPRESS_THRESHOLD:
            QMessageBox.information(
                self, "提示",
                f"当前仅有 {len(history)} 条消息，未达到压缩阈值（{ConversationCompressor.COMPRESS_THRESHOLD} 条）。"
            )
            return

        # 确认对话框
        reply = QMessageBox.question(
            self, "确认压缩",
            f"当前对话共 {len(history)} 条消息。\n\n"
            f"压缩后，AI 将生成一份结构化摘要（保留关键信息），"
            f"同时保留最近 {ConversationCompressor.COMPRESS_THRESHOLD} 条消息保���上下文连贯性。\n\n"
            f"⚠️ 被裁剪的原始消息将无法恢复。\n\n确定要压缩吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 先停止当前正在运行的请求
        self.on_stop_response()

        # 保存当前对话到缓存
        save_current_to_conv(self)

        # 懒加载 compressor 实例
        if self._compressor is None:
            self._compressor = ConversationCompressor(
                api_key=self.api_key,
                base_url=self.base_url,
                model_id=self.model_id,
                proxy=self.proxy
            )

        self.status_label.setText("正在压缩对话历史...")
        QApplication.processEvents()

        try:
            compressed, metadata = self._compressor.compress(history)
        except Exception as e:
            QMessageBox.warning(self, "压缩失败", f"压缩过程出现错误：{str(e)}")
            self.update_status()
            return

        # 写回对话
        if conv:
            conv["history"] = compressed
            save_convs(self)

        # 重新加载当前对话显示
        load_current_conv(self)

        # 反馈结果
        original = metadata.get("original_count", len(history))
        current = len(compressed)
        pct = round((1 - current / original) * 100) if original > 0 else 0

        self.display_message(
            "系统",
            f"✅ 对话压缩完成！消息从 {original} 条缩减至 {current} 条，压缩率约 {pct}%。\n"
            f"摘要已作为上下文保留，最近消息保持完整。",
            "system"
        )
        self.display_message("系统", "提示：可在\"编辑\"菜单中再次压缩以更新摘要。", "system")

    def clear_conversation(self):
        """清空当前对话的消息记录（保留对话本身）"""
        reply = QMessageBox.question(
            self, "确认", "确定要清空当前对话的所有消息吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            conv = get_current_conv(self)
            if conv:
                conv["history"] = []
            self.conversation_history = []
            self.chat_display.clear()
            self.current_tag = None
            self.display_message("系统", "对话内容已清空", "system")
            save_convs(self)
            refresh_conv_list(self)
            self.update_status()

    def export_conversation(self):
        conv = get_current_conv(self)
        if not conv:
            QMessageBox.information(self, "提示", "没有当前对话")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出对话", f"{conv['title']}.json",
            "JSON文件 (*.json);;所有文件 (*)"
        )
        if file_path:
            try:
                if not file_path.lower().endswith('.json'):
                    file_path += '.json'
                data = {
                    "title": conv["title"],
                    "timestamp": conv.get("created_at", ""),
                    "history": conv.get("history", []),
                }
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.display_message("系统", f"对话已导出到: {file_path}", "system")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {e}")

    def import_conversation(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入对话", "", "JSON文件 (*.json);;所有文件 (*)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                conv = {
                    "id": str(uuid.uuid4())[:8],
                    "title": data.get("title", "导入的对话"),
                    "history": data.get("history", []),
                    "created_at": data.get("timestamp", datetime.now().isoformat()),
                }
                self.conversations.insert(0, conv)
                self.current_conv_id = conv["id"]
                save_convs(self)
                refresh_conv_list(self)
                load_current_conv(self)
                self.display_message("系统", f"已导入对话: {conv['title']}", "system")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导入失败: {e}")

    def show_about(self):
        QMessageBox.about(
            self, "关于 AI 对话助手",
            "AI 对话助手 v2.1\n\n"
            "支持多模型、多对话、上下文记忆\n"
            "可选工具调用：计算器、时间查询\n\n"
            "快捷键:\n"
            "  Ctrl+N  新建对话\n"
            "  Ctrl+T  切换上下文记忆\n"
            "  Ctrl+S  导出对话\n"
            "  Ctrl+O  导入对话"
        )

    def closeEvent(self, event):
        if self.worker_thread:
            self.worker_thread.stop()
            self.worker_thread.wait(3000)
            if self.worker_thread.isRunning():
                self.worker_thread.terminate()
                self.worker_thread.wait(1000)
            self.worker_thread = None
        try:
            if getattr(self, "_sched_timer", None):
                self._sched_timer.stop()
        except Exception:
            pass
        save_current_to_conv(self)
        event.accept()

    # ═════════════════════════════════════════
    #  自动化任务管理
    # ═════════════════════════════════════════

    def show_automation_manager(self):
        """打开「自动化任务管理」对话框"""
        dlg = AutomationManagerDialog(self)
        dlg.exec_()


