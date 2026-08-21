"""设置与插件管理弹窗（从 chat_window.py 拆分）。

弹窗 UI 逻辑以 window（ChatWindow 实例）为首个参数，通过 window 访问其
配置字段，并在确认后委托 window 上的轻量配置方法（_save_settings、
setup_client 等）落地。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QFormLayout, QLineEdit, QCheckBox,
                             QSpinBox, QComboBox, QDialogButtonBox, QVBoxLayout,
                             QHBoxLayout, QListWidget, QListWidgetItem, QLabel,
                             QPushButton, QMessageBox)

from .plugin_manager import discover_plugins, get_plugin_meta, compare_versions
from .config import save_plugin_state


def show_settings(window):
    dlg = QDialog(window)
    dlg.setWindowTitle("模型设置")
    dlg.resize(480, 320)
    layout = QFormLayout(dlg)

    le_url = QLineEdit(window.base_url)
    le_url.setPlaceholderText("https://api.openai.com/v1")
    le_key = QLineEdit(window.api_key)
    le_key.setEchoMode(QLineEdit.Password)
    le_key.setPlaceholderText("sk-...")
    le_model = QLineEdit(window.model_id)
    le_model.setPlaceholderText("gpt-4o / DeepSeek-R1-Distill-Qwen-32B")
    chk_think = QCheckBox("启用思考模式 (enable_thinking)")
    chk_think.setChecked(window.enable_thinking)
    chk_tools = QCheckBox("启用工具调用 (计算器、时间查询等)")
    chk_tools.setChecked(window.enable_tools)
    chk_tools.setToolTip("需要模型支持 function calling / tool use 功能。\n如模型不支持，请关闭此选项。")

    # ── 工作区根目录 ──
    ws_layout = QHBoxLayout()
    le_ws = QLineEdit(window.workspace_root or "")
    le_ws.setPlaceholderText("留空=程序所在目录")
    btn_ws = QPushButton("选择...")
    btn_ws.clicked.connect(
        lambda: window._pick_workspace(le_ws))
    ws_layout.addWidget(le_ws)
    ws_layout.addWidget(btn_ws)

    # ── 自主 / Agent 模式 ──
    chk_agent = QCheckBox("自主模式 (提高工具循环轮次，支持多步任务编排)")
    chk_agent.setChecked(window.agent_mode)
    chk_agent.setToolTip("开启后，模型可执行更多轮工具调用（如先检索再执行再整理），"
                         "适合复杂自动化任务。注意会增加 token 消耗。")
    sp_rounds = QSpinBox()
    sp_rounds.setRange(3, 30)
    sp_rounds.setValue(window.max_agent_rounds)
    sp_rounds.setSuffix(" 轮")

    layout.addRow("Base URL:", le_url)
    layout.addRow("API Key:", le_key)
    layout.addRow("模型ID:", le_model)

    # ── 代理设置（仅作用于模型连接）──
    le_proxy = QLineEdit(window.proxy or "")
    le_proxy.setPlaceholderText("http://127.0.0.1:7890 或 socks5://127.0.0.1:7890（留空=不走代理）")
    chk_proxy = QCheckBox("通过代理连接模型地址（仅作用于模型 API，不影响插件/SSH）")
    chk_proxy.setChecked(bool(window.proxy))
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
    le_theme.setCurrentText("浅色" if window.theme == "light" else "深色")
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
        QMessageBox.warning(window, "提示", "Base URL、API Key 和模型ID 不能为空")
        return

    model_changed = (new_url != window.base_url or new_key != window.api_key
                     or new_model != window.model_id
                     or new_proxy != (window.proxy or "")
                     or chk_think.isChecked() != window.enable_thinking
                     or chk_tools.isChecked() != window.enable_tools)
    theme_changed = new_theme != window.theme

    window.base_url = new_url
    window.api_key = new_key
    window.model_id = new_model
    window.proxy = new_proxy
    window.enable_thinking = chk_think.isChecked()
    window.enable_tools = chk_tools.isChecked()
    window.workspace_root = new_ws
    window.agent_mode = new_agent
    window.max_agent_rounds = new_rounds
    window.theme = new_theme
    window._save_settings()

    if model_changed:
        window.setup_client()

    if theme_changed:
        window.apply_theme(rerender_chat=True)

    window.display_message("系统",
        f"设置已更新 — 模型: {window.model_id} | 思考: {'开' if window.enable_thinking else '关'} | 工具: {'开' if window.enable_tools else '关'}",
        "system")


def show_plugin_manager(window):
    """插件管理对话框 — 含版本信息、升级提示、热加载"""
    dlg = QDialog(window)
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
        loaded_ver = window.plugin_infos.get(pname, {}).get("version", "")
        file_ver = meta["version"]
        is_loaded = pname in window.plugins

        if loaded_ver and file_ver and compare_versions(file_ver, loaded_ver) > 0:
            hint = " [文件版本更新，可重新加载]"
        elif not is_loaded:
            hint = " [未加载，点击重新加载]"
        else:
            hint = ""

        mod = window.plugins.get(pname)
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
            Qt.Checked if pname in window.enabled_plugins else Qt.Unchecked
        )
        list_widget.addItem(item)

    layout.addWidget(list_widget)

    # ── 按钮行 ──
    btn_layout = QHBoxLayout()
    btn_reload = QPushButton("🔄 重新加载插件")
    btn_reload.setToolTip("从磁盘重新加载所有插件，替换文件后无需重启程序")
    btn_reload.setCursor(Qt.PointingHandCursor)
    btn_reload.clicked.connect(lambda: reload_plugins(window, list_widget, dlg))
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

    window.enabled_plugins = new_enabled
    save_plugin_state(window.enabled_plugins)

    window.display_message("系统",
        f"插件状态已更新 — 启用: {', '.join(window.enabled_plugins) if window.enabled_plugins else '无'}",
        "system")


def reload_plugins(window, list_widget, dlg):
    """热加载：从磁盘重新扫描并加载所有插件"""
    window.plugins, window.plugin_infos = discover_plugins(reload_modules=True)
    display_message = getattr(window, "display_message", None)

    # 刷新列表
    list_widget.clear()
    metas = get_plugin_meta()
    for pname in sorted(metas.keys()):
        meta = metas[pname]
        is_ok = pname in window.plugins
        status = "" if is_ok else " [加载失败]"

        mod = window.plugins.get(pname)
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
            Qt.Checked if pname in window.enabled_plugins else Qt.Unchecked
        )
        list_widget.addItem(item)

    if display_message:
        display_message("系统",
            f"插件已重新加载 — 共 {len(window.plugins)} 个",
            "system")
