"""ChatWindow — PyQt5 主窗口"""
import os
import sys
import json
import html
import uuid
import threading
from datetime import datetime
from typing import Optional

from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QMenuBar, QMenu,
                             QStatusBar, QMessageBox, QFileDialog, QDialog,
                             QFormLayout, QLabel, QDialogButtonBox, QCheckBox,
                             QListWidget, QListWidgetItem, QFrame,
                             QInputDialog, QMenu, QApplication, QSizePolicy,
                             QComboBox, QSpinBox, QTimeEdit, QDateTimeEdit,
                             QGroupBox)
from PyQt5.QtCore import Qt, QEvent, QPoint, QTimer, QDateTime, QTime, pyqtSignal
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
        self._load_convs()
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
        btn_new.clicked.connect(self.new_conversation)
        title_bar.addWidget(btn_new)
        sidebar_layout.addLayout(title_bar)

        self.conv_list = QListWidget()
        self.conv_list.setObjectName("convList")
        self.conv_list.itemClicked.connect(self.on_conv_selected)
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
        btn_settings.clicked.connect(self.show_settings)
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
            self.new_conversation()
        else:
            self._refresh_conv_list()
            self._load_current_conv()

    # ═══════════════════════════════════════════════
    #  对话管理
    # ═══════════════════════════════════════════════

    def _get_current_conv(self):
        for c in self.conversations:
            if c["id"] == self.current_conv_id:
                return c
        return None

    def new_conversation(self):
        conv = {
            "id": str(uuid.uuid4())[:8],
            "title": "新对话",
            "history": [],
            "created_at": datetime.now().isoformat(),
        }
        self.conversations.insert(0, conv)
        self.current_conv_id = conv["id"]
        self._save_convs()
        self._refresh_conv_list()
        self._load_current_conv()
        self.input_field.setFocus()

    def on_conv_selected(self, item):
        conv_id = item.data(Qt.UserRole)
        if conv_id == self.current_conv_id:
            return
        self._save_current_to_conv()
        self.current_conv_id = conv_id
        self._load_current_conv()
        self._refresh_conv_list()

    def _refresh_conv_list(self):
        pal = self._palette()
        self.conv_list.clear()
        for conv in self.conversations:
            cid = conv["id"]
            title = conv.get("title", "新对话")
            selected = cid == self.current_conv_id

            # 行容器
            row_widget = QWidget()
            row_widget.setObjectName(f"convRow_{cid}")
            bg_color = pal["conv_sel"] if selected else "transparent"
            hover_color = pal["conv_sel"] if selected else pal["conv_hover"]
            row_widget.setStyleSheet(
                f"QWidget#{row_widget.objectName()} {{ background-color: {bg_color}; border-radius: 6px; }}"
                f"QWidget#{row_widget.objectName()}:hover {{ background-color: {hover_color}; }}"
            )

            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(10, 8, 6, 8)
            row_layout.setSpacing(4)

            # 标题标签
            label = QLabel()
            label.setStyleSheet(f"background: transparent; font-size: 13px; color: {pal['conv_fg']};")
            label.setCursor(Qt.PointingHandCursor)
            label.setWordWrap(False)
            # 关键：允许收缩，不被长文本的 minimumSizeHint 撑开
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            label.setMinimumWidth(0)
            label.setToolTip(title)  # 鼠标悬停显示完整标题
            # 截断过长标题，显示省略号（sidebar 240px，按钮区约 50px，留 170px 给标题）
            fm = label.fontMetrics()
            full_text = f"💬 {title}"
            elided = fm.elidedText(full_text, Qt.ElideRight, 170)
            label.setText(elided)
            label.mousePressEvent = lambda e, cid=cid: self._select_conv_by_id(cid)
            row_layout.addWidget(label, 1)

            # "⋯" 按钮
            btn = QPushButton("⋯")
            btn.setObjectName("btnConvMenu")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(28, 28)
            btn.clicked.connect(lambda checked, cid=cid, title=title, btn=btn:
                                self._show_conv_menu(cid, title, btn))
            row_layout.addWidget(btn)

            # 放入 QListWidget
            item = QListWidgetItem()
            item.setData(Qt.UserRole, cid)
            item.setSizeHint(row_widget.sizeHint())
            self.conv_list.addItem(item)
            self.conv_list.setItemWidget(item, row_widget)
            if selected:
                item.setSelected(True)

    def _load_current_conv(self):
        conv = self._get_current_conv()
        self.chat_display.clear()
        self.current_tag = None
        self.conversation_history = []
        if not conv:
            return
        self.conversation_history = list(conv.get("history", []))
        for msg in self.conversation_history:
            role = "您" if msg["role"] == "user" else self.model_id
            tag = "user" if msg["role"] == "user" else "ai"
            self.display_message(role, msg["content"], tag, msg.get("time"))
        self.update_status()

    def _save_current_to_conv(self):
        conv = self._get_current_conv()
        if conv:
            conv["history"] = list(self.conversation_history)
            if not conv.get("title") or conv["title"] == "新对话":
                for m in self.conversation_history:
                    if m["role"] == "user":
                        conv["title"] = m["content"][:30] + ("..." if len(m["content"]) > 30 else "")
                        break
            self._save_convs()
            self._refresh_conv_list()

    def _load_convs(self):
        self.conversations, self.current_conv_id = load_conversations()

    def _save_convs(self):
        save_conversations(self.conversations, self.current_conv_id)

    def _select_conv_by_id(self, conv_id):
        """点击会话标签时切换对话"""
        if conv_id == self.current_conv_id:
            return
        self._save_current_to_conv()
        self.current_conv_id = conv_id
        self._load_current_conv()
        self._refresh_conv_list()

    def _show_conv_menu(self, conv_id, title, button):
        """显示会话的 ⋯ 菜单"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #FFFFFF;
                border: 1px solid #E5E6EB;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                font-size: 13px;
                color: #333;
            }
            QMenu::item:selected {
                background-color: #EEF2FF;
                border-radius: 4px;
            }
        """)
        rename_action = menu.addAction("✏ 重命名")
        menu.addSeparator()
        delete_action = menu.addAction("🗑 删除会话")
        delete_action.setData(conv_id)
        # 弹出菜单在按钮下方
        chosen = menu.exec_(button.mapToGlobal(QPoint(0, button.height())))

        if chosen == rename_action:
            self._rename_conversation(conv_id, title)
        elif chosen == delete_action:
            self._delete_conversation(conv_id)

    def _rename_conversation(self, conv_id, old_title):
        """重命名会话"""
        new_title, ok = QInputDialog.getText(
            self, "重命名会话", "请输入新名称：",
            text=old_title
        )
        if ok and new_title.strip():
            for conv in self.conversations:
                if conv["id"] == conv_id:
                    conv["title"] = new_title.strip()
                    break
            self._save_convs()
            self._refresh_conv_list()

    def _delete_conversation(self, conv_id):
        """删除会话"""
        reply = QMessageBox.question(
            self, "确认", "确定要删除这个会话吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self.conversations = [c for c in self.conversations if c["id"] != conv_id]
        # 如果删的是当前对话，切换到第一个
        if conv_id == self.current_conv_id:
            if self.conversations:
                self.current_conv_id = self.conversations[0]["id"]
            else:
                self.new_conversation()
                return
        self._save_convs()
        self._load_current_conv()
        self._refresh_conv_list()
        self.display_message("系统", "会话已删除", "system")

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

    def show_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("模型设置")
        dlg.resize(480, 320)
        layout = QFormLayout(dlg)

        le_url = QLineEdit(self.base_url)
        le_url.setPlaceholderText("https://api.openai.com/v1")
        le_key = QLineEdit(self.api_key)
        le_key.setEchoMode(QLineEdit.Password)
        le_key.setPlaceholderText("sk-...")
        le_model = QLineEdit(self.model_id)
        le_model.setPlaceholderText("gpt-4o / DeepSeek-R1-Distill-Qwen-32B")
        chk_think = QCheckBox("启用思考模式 (enable_thinking)")
        chk_think.setChecked(self.enable_thinking)
        chk_tools = QCheckBox("启用工具调用 (计算器、时间查询等)")
        chk_tools.setChecked(self.enable_tools)
        chk_tools.setToolTip("需要模型支持 function calling / tool use 功能。\n如模型不支持，请关闭此选项。")

        # ── 工作区根目录 ──
        ws_layout = QHBoxLayout()
        le_ws = QLineEdit(self.workspace_root or "")
        le_ws.setPlaceholderText("留空=程序所在目录")
        btn_ws = QPushButton("选择...")
        btn_ws.clicked.connect(
            lambda: self._pick_workspace(le_ws))
        ws_layout.addWidget(le_ws)
        ws_layout.addWidget(btn_ws)

        # ── 自主 / Agent 模式 ──
        chk_agent = QCheckBox("自主模式 (提高工具循环轮次，支持多步任务编排)")
        chk_agent.setChecked(self.agent_mode)
        chk_agent.setToolTip("开启后，模型可执行更多轮工具调用（如先检索再执行再整理），"
                             "适合复杂自动化任务。注意会增加 token 消耗。")
        sp_rounds = QSpinBox()
        sp_rounds.setRange(3, 30)
        sp_rounds.setValue(self.max_agent_rounds)
        sp_rounds.setSuffix(" 轮")

        layout.addRow("Base URL:", le_url)
        layout.addRow("API Key:", le_key)
        layout.addRow("模型ID:", le_model)

        # ── 代理设置（仅作用于模型连接）──
        le_proxy = QLineEdit(self.proxy or "")
        le_proxy.setPlaceholderText("http://127.0.0.1:7890 或 socks5://127.0.0.1:7890（留空=不走代理）")
        chk_proxy = QCheckBox("通过代理连接模型地址（仅作用于模型 API，不影响插件/SSH）")
        chk_proxy.setChecked(bool(self.proxy))
        chk_proxy.setToolTip("公司内网需经代理访问模型服务时填写。\n该代理只注入模型客户端，"
                             "插件自身的 HTTP/SSH 等连接不受影响。")
        layout.addRow("代理地址:", le_proxy)
        layout.addRow("", chk_proxy)

        layout.addRow("", chk_think)
        layout.addRow("", chk_tools)
        layout.addRow("工作区根目录:", ws_layout)
        layout.addRow("", chk_agent)
        layout.addRow("自主模式轮次:", sp_rounds)

        # ── 界面主题 ──
        le_theme = QComboBox()
        le_theme.addItems(["浅色", "深色"])
        le_theme.setCurrentText("浅色" if self.theme == "light" else "深色")
        layout.addRow("界面主题:", le_theme)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return

        new_url = le_url.text().strip()
        new_key = le_key.text().strip()
        new_model = le_model.text().strip()
        new_ws = le_ws.text().strip()
        new_agent = chk_agent.isChecked()
        new_rounds = sp_rounds.value()
        new_proxy = le_proxy.text().strip() if chk_proxy.isChecked() else ""
        new_theme = "light" if le_theme.currentText() == "浅色" else "dark"

        if not new_url or not new_key or not new_model:
            QMessageBox.warning(self, "提示", "Base URL、API Key 和模型ID 不能为空")
            return

        model_changed = (new_url != self.base_url or new_key != self.api_key
                         or new_model != self.model_id
                         or new_proxy != (self.proxy or "")
                         or chk_think.isChecked() != self.enable_thinking
                         or chk_tools.isChecked() != self.enable_tools)
        changed = model_changed or new_ws != (self.workspace_root or "") \
            or new_agent != self.agent_mode or new_rounds != self.max_agent_rounds
        theme_changed = new_theme != self.theme

        self.base_url = new_url
        self.api_key = new_key
        self.model_id = new_model
        self.proxy = new_proxy
        self.enable_thinking = chk_think.isChecked()
        self.enable_tools = chk_tools.isChecked()
        self.workspace_root = new_ws
        self.agent_mode = new_agent
        self.max_agent_rounds = new_rounds
        self.theme = new_theme
        self._save_settings()

        if model_changed:
            self.setup_client()

        if theme_changed:
            self.apply_theme(rerender_chat=True)

        self.display_message("系统",
            f"设置已更新 — 模型: {self.model_id} | 思考: {'开' if self.enable_thinking else '关'} | 工具: {'开' if self.enable_tools else '关'}",
            "system")

    def show_plugin_manager(self):
        """插件管理对话框 — 含版本信息、升级提示、热加载"""
        dlg = QDialog(self)
        dlg.setWindowTitle("插件管理")
        dlg.resize(550, 460)
        layout = QVBoxLayout(dlg)

        # ── 顶部说明 ──
        info_label = QLabel("勾选启用/禁用插件。替换 .py 文件后点击「重新加载」即可热更新，无需重启。")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # ── 插件列表 ──
        list_widget = QListWidget()

        # 扫描元信息（不加载模块，避免热加载冲突）
        metas = get_plugin_meta()

        for pname in sorted(metas.keys()):
            meta = metas[pname]
            # 当前已加载的版本（如果已加载）
            loaded_ver = self.plugin_infos.get(pname, {}).get("version", "")
            file_ver = meta["version"]
            is_loaded = pname in self.plugins

            # 版本比较
            if loaded_ver and file_ver and compare_versions(file_ver, loaded_ver) > 0:
                hint = " [文件版本更新，可重新加载]"
            elif not is_loaded:
                hint = " [未加载，点击重新加载]"
            else:
                hint = ""

            # 插件类型
            mod = self.plugins.get(pname)
            if mod:
                has_tools = hasattr(mod, "TOOLS") and hasattr(mod, "execute") and getattr(mod, "TOOLS", [])
                has_skill = hasattr(mod, "SYSTEM_PROMPT") and getattr(mod, "SYSTEM_PROMPT", "").strip()
                if has_tools and has_skill:
                    tag = " [🔧 工具 + 📋 技能]"
                elif has_tools:
                    tag = " [🔧 工具]"
                elif has_skill:
                    tag = " [📋 技能]"
                else:
                    tag = ""
            else:
                tag = ""

            label_text = (
                f"{pname}  v{file_ver}{tag}"
                f"{hint}\n"
                f"  {meta['description']}  ({meta['size_kb']}KB)"
            )

            item = QListWidgetItem(label_text)
            item.setData(Qt.UserRole, pname)
            item.setCheckState(
                Qt.Checked if pname in self.enabled_plugins else Qt.Unchecked
            )
            list_widget.addItem(item)

        layout.addWidget(list_widget)

        # ── 按钮行 ──
        btn_layout = QHBoxLayout()
        btn_reload = QPushButton("🔄 重新加载插件")
        btn_reload.setToolTip("从磁盘重新加载所有插件，替换文件后无需重启程序")
        btn_reload.setCursor(Qt.PointingHandCursor)
        btn_reload.clicked.connect(lambda: self._reload_plugins(list_widget, dlg))
        btn_layout.addWidget(btn_reload)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # ── 底部按钮 ──
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return

        new_enabled = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            pname = item.data(Qt.UserRole)
            if item.checkState() == Qt.Checked:
                new_enabled.append(pname)

        self.enabled_plugins = new_enabled
        save_plugin_state(self.enabled_plugins)

        self.display_message("系统",
            f"插件状态已更新 — 启用: {', '.join(self.enabled_plugins) if self.enabled_plugins else '无'}",
            "system")

    def _reload_plugins(self, list_widget, dlg):
        """热加载：从磁盘重新扫描并加载所有插件"""
        self.plugins, self.plugin_infos = discover_plugins(reload_modules=True)
        display_message = getattr(self, "display_message", None)

        # 刷新列表
        list_widget.clear()
        metas = get_plugin_meta()
        for pname in sorted(metas.keys()):
            meta = metas[pname]
            is_ok = pname in self.plugins
            status = "" if is_ok else " [加载失败]"

            # 插件类型
            mod = self.plugins.get(pname)
            if mod:
                has_tools = hasattr(mod, "TOOLS") and hasattr(mod, "execute") and getattr(mod, "TOOLS", [])
                has_skill = hasattr(mod, "SYSTEM_PROMPT") and getattr(mod, "SYSTEM_PROMPT", "").strip()
                if has_tools and has_skill:
                    tag = " [🔧 工具 + 📋 技能]"
                elif has_tools:
                    tag = " [🔧 工具]"
                elif has_skill:
                    tag = " [📋 技能]"
                else:
                    tag = ""
            else:
                tag = ""

            label_text = (
                f"{pname}  v{meta['version']}{tag}{status}\n"
                f"  {meta['description']}  ({meta['size_kb']}KB)"
            )
            item = QListWidgetItem(label_text)
            item.setData(Qt.UserRole, pname)
            item.setCheckState(
                Qt.Checked if pname in self.enabled_plugins else Qt.Unchecked
            )
            list_widget.addItem(item)

        if display_message:
            display_message("系统",
                f"插件已重新加载 — 共 {len(self.plugins)} 个",
                "system")

    def create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("新建对话", self.new_conversation, "Ctrl+N")
        file_menu.addAction("导出对话", self.export_conversation, "Ctrl+S")
        file_menu.addAction("导入对话", self.import_conversation, "Ctrl+O")
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close, "Alt+F4")

        edit_menu = menubar.addMenu("编辑")
        edit_menu.addAction("清空对话内容", self.clear_conversation)
        edit_menu.addAction("压缩对话历史", self.compress_conversation, "Ctrl+Shift+C")
        edit_menu.addAction("切换上下文记忆", self.toggle_context, "Ctrl+T")

        settings_menu = menubar.addMenu("设置")
        settings_menu.addAction("模型设置...", self.show_settings)
        settings_menu.addAction("插件管理...", self.show_plugin_manager)

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
        """事件过滤器：QApplication 级别拦截 Ctrl+C 优先复制 chat_display 文本；
        chat_display 级别拦截文本输入事件"""
        etype = event.type()

        # ── QApplication 级别：全局拦截 Ctrl+C ──
        # 因为 chat_display 设置了 NoFocus，键盘事件不会发给它。
        # 必须在 QApplication 级别拦截，优先级高于 QLineEdit 等控件的内部处理。
        if obj is not self.chat_display and etype == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if (key == Qt.Key_C and modifiers == Qt.ControlModifier
                    and obj is not self.input_field):
                # 优先检查 chat_display 是否有选中文本
                cursor = self.chat_display.textCursor()
                if cursor.hasSelection():
                    clipboard = QApplication.clipboard()
                    clipboard.setText(cursor.selectedText())
                    self.status_label.setText("已复制选中内容")
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(1500, self.update_status)
                    return True  # 消费事件，阻止继续传播
                # chat_display 无选中文本时，不拦截，让其他控件正常处理复制

        # ── chat_display 级别：拦截文本编辑事件 ──
        if obj is not self.chat_display:
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
            cursor.insertHtml(self._build_bubble(
                self._cur_sender, clean, tag, self._cur_time))
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
        self._save_current_to_conv()
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

    def _palette(self):
        """返回当前主题下的配色（用于气泡与列表）。"""
        if self.theme == "dark":
            return {
                "chat_bg": "#161616",
                "ai_bubble": "#2A2A2A", "ai_fg": "#E8E8E8",
                "user_bubble": "#2F7D5B", "user_fg": "#FFFFFF",
                "tool_bg": "#3A3020", "tool_fg": "#E0C089",
                "sys_bg": "#2A2A2A", "sys_fg": "#9A9A9A",
                "err_bg": "#3A1F1F", "err_fg": "#FF9A9A",
                "think_bg": "#242424", "think_fg": "#9A9A9A",
                "avatar_ai": "#4E6EF2", "avatar_user": "#23B36B",
                "avatar_tool": "#E0913A", "avatar_think": "#7A7FB0",
                "ts_fg": "#777777",
                "conv_sel": "#34406B", "conv_hover": "#2A2E38", "conv_fg": "#E5E5E5",
            }
        return {
            "chat_bg": "#F5F5F7",
            "ai_bubble": "#FFFFFF", "ai_fg": "#1F1F1F",
            "user_bubble": "#95EC69", "user_fg": "#1A1A1A",
            "tool_bg": "#FFF7E6", "tool_fg": "#9A6B1A",
            "sys_bg": "#EDEFF2", "sys_fg": "#8A8F99",
            "err_bg": "#FDECEC", "err_fg": "#D93025",
            "think_bg": "#F0F1F3", "think_fg": "#8A8F99",
            "avatar_ai": "#4E6EF2", "avatar_user": "#23B36B",
            "avatar_tool": "#E0913A", "avatar_think": "#9AA0C0",
            "ts_fg": "#B0B4BD",
            "conv_sel": "#D6E4FF", "conv_hover": "#EEF2FF", "conv_fg": "#333333",
        }

    def _theme_qss(self, theme):
        """返回整套界面 QSS（浅色 / 深色）。"""
        if theme == "dark":
            return """
            QMainWindow { background-color: #1A1A1A; }
            QFrame#sidebar { background-color: #202020; border-right: 1px solid #2C2C2C; }
            QPushButton#btnNewChat { background-color: #2A2A2A; border: 1px solid #3A3A3A; border-radius: 8px; padding: 8px 16px; font-size: 13px; color: #E5E5E5; text-align: left; }
            QPushButton#btnNewChat:hover { background-color: #2F3350; border-color: #4E6EF2; }
            QListWidget#convList { border: none; background: transparent; font-size: 13px; outline: none; padding: 4px 4px; }
            QListWidget#convList::item { background: transparent; padding: 0px; margin: 0px; }
            QLabel#sidebarTitle { font-size: 14px; font-weight: bold; padding: 16px 16px 8px 16px; color: #E5E5E5; }
            QPushButton#btnConvMenu { background: transparent; border: none; font-size: 14px; color: #888888; padding: 4px 6px; border-radius: 4px; }
            QPushButton#btnConvMenu:hover { background-color: #333333; color: #E5E5E5; }
            QFrame#inputFrame { background-color: #1A1A1A; border-top: 1px solid #2C2C2C; }
            QLineEdit { border: 1px solid #3A3A3A; border-radius: 18px; padding: 10px 16px; font-size: 13px; background: #262626; min-height: 20px; color: #E5E5E5; }
            QLineEdit:focus { border-color: #4E6EF2; }
            QPushButton#btnSend { background-color: #4E6EF2; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
            QPushButton#btnSend:hover { background-color: #3B5BEF; }
            QPushButton#btnSend:pressed { background-color: #2D4CD9; }
            QPushButton#btnSend:disabled { background-color: #3A3F55; }
            QPushButton#btnStop { background-color: #E04848; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
            QPushButton#btnStop:hover { background-color: #CC3333; }
            QWidget#expertBar { background-color: #1E1E1E; border-bottom: 1px solid #2C2C2C; }
            QLabel#expertLabel { font-size: 12px; color: #AAAAAA; }
            QComboBox#expertCombo { border: 1px solid #3A3A3A; border-radius: 6px; padding: 4px 10px; font-size: 12px; color: #E5E5E5; background: #262626; min-height: 20px; }
            QComboBox#expertCombo:focus { border-color: #4E6EF2; }
            QComboBox#expertCombo:on { border-color: #4E6EF2; }
            QComboBox#expertCombo QAbstractItemView { font-size: 12px; background: #262626; border: 1px solid #3A3A3A; border-radius: 6px; outline: 0; selection-background-color: #34406B; selection-color: #E5E5E5; }
            QComboBox#expertCombo QAbstractItemView::item { color: #E5E5E5; padding: 6px 12px; }
            QComboBox#expertCombo QAbstractItemView::item:selected { color: #E5E5E5; background: #34406B; }
            QComboBox#expertCombo QAbstractItemView::item:hover { color: #E5E5E5; background: #2E3340; }
            QTextEdit#chatDisplay { font-family: 'Microsoft YaHei'; font-size: 13px; border: none; background-color: #161616; color: #E8E8E8; }
            QStatusBar { background: #1A1A1A; color: #AAAAAA; }
            QStatusBar::item { border: none; }
            QMenu { background-color: #262626; border: 1px solid #3A3A3A; border-radius: 6px; padding: 4px; }
            QMenu::item { padding: 8px 24px; font-size: 13px; color: #E5E5E5; }
            QMenu::item:selected { background-color: #34406B; border-radius: 4px; }
            """
        return """
            QMainWindow { background-color: #FFFFFF; }
            QFrame#sidebar { background-color: #F7F8FA; border-right: 1px solid #E5E6EB; }
            QPushButton#btnNewChat { background-color: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 8px; padding: 8px 16px; font-size: 13px; color: #333333; text-align: left; }
            QPushButton#btnNewChat:hover { background-color: #EEF2FF; border-color: #4E6EF2; }
            QListWidget#convList { border: none; background: transparent; font-size: 13px; outline: none; padding: 4px 4px; }
            QListWidget#convList::item { background: transparent; padding: 0px; margin: 0px; }
            QLabel#sidebarTitle { font-size: 14px; font-weight: bold; padding: 16px 16px 8px 16px; color: #333333; }
            QPushButton#btnConvMenu { background: transparent; border: none; font-size: 14px; color: #999999; padding: 4px 6px; border-radius: 4px; }
            QPushButton#btnConvMenu:hover { background-color: #E5E6EB; color: #333333; }
            QFrame#inputFrame { background-color: #F7F8FA; border-top: 1px solid #E5E6EB; }
            QLineEdit { border: 1px solid #E5E6EB; border-radius: 18px; padding: 10px 16px; font-size: 13px; background: #FFFFFF; min-height: 20px; color: #333333; }
            QLineEdit:focus { border-color: #4E6EF2; }
            QPushButton#btnSend { background-color: #4E6EF2; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
            QPushButton#btnSend:hover { background-color: #3B5BEF; }
            QPushButton#btnSend:pressed { background-color: #2D4CD9; }
            QPushButton#btnSend:disabled { background-color: #B0B8D0; }
            QPushButton#btnStop { background-color: #E04848; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
            QPushButton#btnStop:hover { background-color: #CC3333; }
            QWidget#expertBar { background-color: #F7F8FA; border-bottom: 1px solid #E5E6EB; }
            QLabel#expertLabel { font-size: 12px; color: #666666; }
            QComboBox#expertCombo { border: 1px solid #E5E6EB; border-radius: 6px; padding: 4px 10px; font-size: 12px; color: #333333; background: #FFFFFF; min-height: 20px; }
            QComboBox#expertCombo:focus { border-color: #4E6EF2; }
            QComboBox#expertCombo:on { border-color: #4E6EF2; }
            QComboBox#expertCombo QAbstractItemView { font-size: 12px; background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; outline: 0; selection-background-color: #EEF1FF; selection-color: #333333; }
            QComboBox#expertCombo QAbstractItemView::item { color: #333333; padding: 6px 12px; }
            QComboBox#expertCombo QAbstractItemView::item:selected { color: #333333; background: #EEF1FF; }
            QComboBox#expertCombo QAbstractItemView::item:hover { color: #333333; background: #F2F4F8; }
            QTextEdit#chatDisplay { font-family: 'Microsoft YaHei'; font-size: 13px; border: none; background-color: #F5F5F7; color: #1F1F1F; }
            QStatusBar { background: #F7F8FA; color: #666666; }
            QStatusBar::item { border: none; }
            QMenu { background-color: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; padding: 4px; }
            QMenu::item { padding: 8px 24px; font-size: 13px; color: #333333; }
            QMenu::item:selected { background-color: #EEF2FF; border-radius: 4px; }
            """

    def apply_theme(self, rerender_chat=True):
        """应用浅色 / 深色主题：注入 QSS，并按需重渲染会话列表与聊天气泡。"""
        self.setStyleSheet(self._theme_qss(self.theme))
        self.status_label.setStyleSheet(
            f"color: {self._palette()['ts_fg']}; padding: 0 8px;")
        self._refresh_conv_list()
        if rerender_chat:
            self._load_current_conv()

    def _toggle_theme(self):
        """侧边栏 🌓 按钮：在浅色 / 深色之间切换并持久化。"""
        self.theme = "dark" if self.theme == "light" else "light"
        self._save_settings()
        self.apply_theme(rerender_chat=True)

    def _avatar_td(self, bg, icon):
        return (f'<td width="36" align="center" style="vertical-align:top;">'
                f'<div style="width:34px;height:34px;border-radius:17px;'
                f'background:{bg};color:#ffffff;text-align:center;'
                f'font-size:18px;line-height:34px;">{icon}</div></td>')

    def _build_bubble(self, sender, text, tag, time_str):
        p = self._palette()
        esc = html.escape(text).replace("\n", "<br>")
        ts_fg = p["ts_fg"]
        mono = 'font-family:"Consolas","Courier New",monospace;font-size:12px;'
        spacer = '<td width="10%"></td>'

        if tag == "user":
            bubble = ('<td width="72%" align="right" style="background:{ub};'
                      'color:{uf};border-radius:16px;padding:9px 13px;'
                      'font-family:"Microsoft YaHei";font-size:13px;line-height:1.55;">{esc}</td>'
                      ).format(ub=p["user_bubble"], uf=p["user_fg"], esc=esc)
            av = self._avatar_td(p["avatar_user"], "🧑")
            row = f'<tr>{spacer}{bubble}{av}</tr>'
        elif tag == "tool":
            bubble = ('<td width="72%" align="left" style="background:{tb};'
                      'color:{tf};border-radius:12px;padding:8px 12px;{mono}'
                      'line-height:1.5;">{esc}</td>'
                      ).format(tb=p["tool_bg"], tf=p["tool_fg"], mono=mono, esc=esc)
            av = self._avatar_td(p["avatar_tool"], "🔧")
            row = f'<tr>{av}{bubble}{spacer}</tr>'
        elif tag == "thinking":
            bubble = ('<td width="72%" align="left" style="background:{kb};'
                      'color:{kf};border-radius:14px;padding:9px 13px;'
                      'font-family:"Microsoft YaHei";font-size:13px;line-height:1.55;'
                      'font-style:italic;">{esc}</td>'
                      ).format(kb=p["think_bg"], kf=p["think_fg"], esc=esc)
            av = self._avatar_td(p["avatar_think"], "💭")
            row = f'<tr>{av}{bubble}{spacer}</tr>'
        elif tag == "error":
            bubble = ('<td width="80%" align="left" style="background:{eb};'
                      'color:{ef};border-radius:12px;padding:8px 12px;'
                      'font-family:"Microsoft YaHei";font-size:13px;line-height:1.5;">{esc}</td>'
                      ).format(eb=p["err_bg"], ef=p["err_fg"], esc=esc)
            row = f'<tr>{spacer}{bubble}<td width="10%"></td></tr>'
        elif tag == "system":
            # 系统提示：时间戳 + 居中胶囊，整行放入单一表格
            return (f'<table width="100%" cellpadding="3" cellspacing="0">'
                    f'<tr><td align="center" style="color:{ts_fg};font-size:11px;'
                    f'font-family:"Microsoft YaHei";padding:4px 0 2px 0;">{time_str}</td></tr>'
                    f'<tr><td align="center"><span style="background:{p["sys_bg"]};'
                    f'color:{p["sys_fg"]};padding:3px 12px;border-radius:10px;'
                    f'font-size:12px;font-family:"Microsoft YaHei";">{esc}</span></td></tr>'
                    f'</table>')
        else:  # 默认：AI 回复（左侧，机器人头像）
            bubble = ('<td width="72%" align="left" style="background:{ab};'
                      'color:{af};border-radius:16px;padding:9px 13px;'
                      'font-family:"Microsoft YaHei";font-size:13px;line-height:1.55;">{esc}</td>'
                      ).format(ab=p["ai_bubble"], af=p["ai_fg"], esc=esc)
            av = self._avatar_td(p["avatar_ai"], "🤖")
            row = f'<tr>{av}{bubble}{spacer}</tr>'

        # 时间戳作为表格首行，用 <td align="center"> 承载（Qt 可靠居中）
        return (f'<table width="100%" cellpadding="3" cellspacing="0">'
                f'<tr><td align="center" colspan="3" style="color:{ts_fg};'
                f'font-size:11px;font-family:"Microsoft YaHei";'
                f'padding:6px 0 2px 0;">{time_str}</td></tr>'
                f'{row}</table>')

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
        cursor.insertHtml(self._build_bubble(sender, message, tag or "ai", time_str))
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
        conv = self._get_current_conv()
        if conv:
            conv_title = conv["title"]
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
        conv = self._get_current_conv()
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
        self._save_current_to_conv()

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
            self._save_convs()

        # 重新加载当前对话显示
        self._load_current_conv()

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
            conv = self._get_current_conv()
            if conv:
                conv["history"] = []
            self.conversation_history = []
            self.chat_display.clear()
            self.current_tag = None
            self.display_message("系统", "对话内容已清空", "system")
            self._save_convs()
            self._refresh_conv_list()
            self.update_status()

    def export_conversation(self):
        conv = self._get_current_conv()
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
                self._save_convs()
                self._refresh_conv_list()
                self._load_current_conv()
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
        self._save_current_to_conv()
        event.accept()

    # ═════════════════════════════════════════
    #  自动化任务管理
    # ═════════════════════════════════════════

    def show_automation_manager(self):
        """打开「自动化任务管理」对话框"""
        dlg = AutomationManagerDialog(self)
        dlg.exec_()


class AutomationManagerDialog(QDialog):
    """自动化任务列表：新建 / 编辑 / 删除 / 立即运行 / 查看日志 / 刷新"""

    # 跨线程安全回调：worker 线程执行完后用信号把结果发回 GUI 线程
    _run_done = pyqtSignal(str, str)  # (final, error)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._run_done.connect(self._on_done)
        self.setWindowTitle("自动化任务管理")
        self.resize(760, 500)
        layout = QVBoxLayout(self)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._edit_selected)
        layout.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_edit = QPushButton("编辑")
        self.btn_del = QPushButton("删除")
        self.btn_run = QPushButton("立即运行")
        self.btn_log = QPushButton("查看日志")
        self.btn_refresh = QPushButton("刷新")
        for b in (self.btn_new, self.btn_edit, self.btn_del,
                  self.btn_run, self.btn_log, self.btn_refresh):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.status = QLabel("")
        layout.addWidget(self.status)

        self.btn_new.clicked.connect(self._new)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_del.clicked.connect(self._delete)
        self.btn_run.clicked.connect(self._run_now)
        self.btn_log.clicked.connect(self._view_logs)
        self.btn_refresh.clicked.connect(self.refresh)

        self.refresh()

    def _items(self):
        return self.parent_window.scheduler.automations

    def refresh(self):
        self.list.clear()
        for a in self._items():
            sch = a.get("schedule", {})
            enabled = a.get("enabled", True)
            last = a.get("last_run")
            status = a.get("last_status")
            last_txt = ""
            if last:
                st = "✓" if status == "ok" else ("✗" if status == "error" else "?")
                last_txt = f" | 上次: {st} {last}"
            mark = "●" if enabled else "○"
            text = (f"{mark} {a.get('name', '?')}  "
                   f"〔{describe_schedule(sch)}〕{last_txt}")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, a.get("id"))
            self.list.addItem(item)

    def _selected_id(self):
        item = self.list.currentItem()
        if item:
            return item.data(Qt.UserRole)
        return None

    def _new(self):
        dlg = AutomationEditDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.parent_window.scheduler.add_automation(
                name=dlg.name_edit.text().strip(),
                prompt=dlg.prompt_edit.toPlainText().strip(),
                schedule=dlg.get_schedule(),
                enabled=dlg.enabled_chk.isChecked(),
                max_rounds=dlg.rounds_spin.value(),
            )
            self.refresh()

    def _edit_selected(self):
        aid = self._selected_id()
        if not aid:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        auto = next((a for a in self._items() if a.get("id") == aid), None)
        if not auto:
            return
        dlg = AutomationEditDialog(self, auto)
        if dlg.exec_() == QDialog.Accepted:
            self.parent_window.scheduler.update_automation(
                aid,
                name=dlg.name_edit.text().strip(),
                prompt=dlg.prompt_edit.toPlainText().strip(),
                schedule=dlg.get_schedule(),
                enabled=dlg.enabled_chk.isChecked(),
                max_rounds=dlg.rounds_spin.value(),
            )
            self.refresh()

    def _delete(self):
        aid = self._selected_id()
        if not aid:
            return
        name = next((a.get("name") for a in self._items()
                   if a.get("id") == aid), aid)
        if QMessageBox.question(
                self, "确认删除",
                f"确定删除任务「{name}」？\n（已生成的执行记录日志会保留）",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.parent_window.scheduler.delete_automation(aid)
            self.refresh()

    def _run_now(self):
        aid = self._selected_id()
        if not aid:
            return
        self.status.setText("执行中…")
        self.btn_run.setEnabled(False)

        def _worker():
            final, err = self.parent_window.scheduler.run_now(aid)
            # 用信号跨线程安全回调（QTimer.singleShot 在 worker 线程里不会触发）
            self._run_done.emit(final, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, final, err):
        self.btn_run.setEnabled(True)
        self.refresh()
        if err:
            self.status.setText(f"执行失败: {err[:120]}")
            QMessageBox.warning(self, "执行失败", err[:500])
        else:
            self.status.setText("执行完成")
            msg = final[:800] + ("…" if len(final) > 800 else "")
            QMessageBox.information(self, "执行结果", msg)

    def _view_logs(self):
        aid = self._selected_id()
        if not aid:
            return
        auto = next((a for a in self._items() if a.get("id") == aid), None)
        dlg = LogViewDialog(self, auto, self.parent_window.scheduler)
        dlg.exec_()


class AutomationEditDialog(QDialog):
    """新建 / 编辑单个自动化任务：名称、提示词、周期、启用、轮次"""

    def __init__(self, parent=None, auto=None):
        super().__init__(parent)
        self.auto = auto
        self.setWindowTitle("编辑自动化任务" if auto else "新建自动化任务")
        self.resize(580, 540)
        layout = QFormLayout(self)

        self.name_edit = QLineEdit()
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMinimumHeight(160)
        self.enabled_chk = QCheckBox("启用此任务")
        self.enabled_chk.setChecked(True)
        self.rounds_spin = QSpinBox()
        self.rounds_spin.setRange(1, 50)
        self.rounds_spin.setValue(12)

        layout.addRow("任务名称:", self.name_edit)
        layout.addRow("任务提示词:", self.prompt_edit)
        layout.addRow("工具循环轮次:", self.rounds_spin)
        layout.addRow("", self.enabled_chk)

        # ── 周期设置 ──
        self.type_combo = QComboBox()
        self.type_combo.addItems(["interval", "daily", "weekly", "once"])
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        layout.addRow("周期类型:", self.type_combo)

        self.sched_widget = QWidget()
        self.sched_layout = QFormLayout(self.sched_widget)
        layout.addRow(self.sched_widget)

        # interval
        self.every_spin = QSpinBox()
        self.every_spin.setRange(1, 9999)
        self.every_spin.setValue(1)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["minutes", "hours", "days"])
        self.interval_row = self._row_widget([self.every_spin, self.unit_combo])

        # daily / weekly 时间
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")

        # weekly 星期
        self.day_checks = {}
        day_box = QGroupBox("星期（勾选执行日）")
        day_layout = QHBoxLayout(day_box)
        for i, nm in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            cb = QCheckBox("周" + nm)
            cb.setChecked(i < 5)
            self.day_checks[i] = cb
            day_layout.addWidget(cb)
        self.weekday_box = day_box

        # once 时间
        self.datetime_edit = QDateTimeEdit()
        self.datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.datetime_edit.setDateTime(QDateTime.currentDateTime())

        self.sched_layout.addRow("间隔:", self.interval_row)
        self.sched_layout.addRow("时间:", self.time_edit)
        self.sched_layout.addRow("", self.weekday_box)
        self.sched_layout.addRow("执行时间:", self.datetime_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

        if auto:
            self._load(auto)
        self._on_type_changed(self.type_combo.currentText())

    def _row_widget(self, widgets):
        w = QWidget()
        h = QHBoxLayout(w)
        for x in widgets:
            h.addWidget(x)
        return w

    def _load(self, auto):
        self.name_edit.setText(auto.get("name", ""))
        self.prompt_edit.setPlainText(auto.get("prompt", ""))
        self.enabled_chk.setChecked(auto.get("enabled", True))
        self.rounds_spin.setValue(auto.get("max_rounds", 12))
        sch = auto.get("schedule", {})
        self.type_combo.setCurrentText(sch.get("type", "interval"))
        self.every_spin.setValue(sch.get("every", 1))
        self.unit_combo.setCurrentText(sch.get("unit", "minutes"))
        if sch.get("time"):
            h, m = sch["time"].split(":")
            self.time_edit.setTime(QTime(int(h), int(m)))
        for i, cb in self.day_checks.items():
            cb.setChecked(i in sch.get("weekdays", []))
        if sch.get("datetime"):
            self.datetime_edit.setDateTime(
                QDateTime.fromString(sch["datetime"], "yyyy-MM-dd HH:mm"))

    def _on_type_changed(self, typ):
        self.interval_row.setVisible(typ == "interval")
        self.time_edit.setVisible(typ in ("daily", "weekly"))
        self.weekday_box.setVisible(typ == "weekly")
        self.datetime_edit.setVisible(typ == "once")

    def get_schedule(self):
        typ = self.type_combo.currentText()
        if typ == "interval":
            return {"type": "interval", "every": self.every_spin.value(),
                    "unit": self.unit_combo.currentText()}
        if typ == "daily":
            return {"type": "daily",
                    "time": self.time_edit.time().toString("HH:mm")}
        if typ == "weekly":
            days = [i for i, cb in self.day_checks.items() if cb.isChecked()]
            return {"type": "weekly",
                    "time": self.time_edit.time().toString("HH:mm"),
                    "weekdays": days}
        if typ == "once":
            return {"type": "once",
                    "datetime": self.datetime_edit.dateTime().toString("yyyy-MM-dd HH:mm")}
        return {"type": "interval", "every": 1, "unit": "minutes"}


class LogViewDialog(QDialog):
    """查看某个任务的历史执行记录（每次执行一个 .md 全文）"""

    def __init__(self, parent=None, auto=None, scheduler=None):
        super().__init__(parent)
        self.auto = auto
        self.scheduler = scheduler
        self.setWindowTitle(
            f"执行记录 — {auto.get('name', '?')}" if auto else "执行记录")
        self.resize(760, 540)
        layout = QHBoxLayout(self)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._show_current)
        layout.addWidget(self.list, 1)

        right = QVBoxLayout()
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.NoWrap)
        right.addWidget(self.view, 1)
        layout.addLayout(right, 2)

        if auto and scheduler:
            runs = scheduler.list_runs(auto.get("id"))
            for r in runs:
                item = QListWidgetItem(
                    f"{r.get('started_at', '')[:16]}  "
                    f"{'✓' if r.get('status') == 'ok' else '✗'}  "
                    f"({r.get('output_chars', 0)}字 / "
                    f"{r.get('tool_calls', 0)}次工具)")
                item.setData(Qt.UserRole, r.get("run_id"))
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
            self._show_current()

    def _show_current(self):
        item = self.list.currentItem()
        if not item:
            return
        run_id = item.data(Qt.UserRole)
        content = self.scheduler.read_log(run_id)
        self.view.setPlainText(content or "(无日志内容)")
