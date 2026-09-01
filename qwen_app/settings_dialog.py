"""设置与插件管理弹窗（从 chat_window.py 拆分）。

弹窗 UI 逻辑以 window（ChatWindow 实例）为首个参数，通过 window 访问其
配置字段，并在确认后委托 window 上的轻量配置方法（_save_settings、
setup_client 等）落地。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QFormLayout, QLineEdit, QCheckBox,
                             QSpinBox, QComboBox, QDialogButtonBox, QVBoxLayout,
                             QHBoxLayout, QListWidget, QListWidgetItem, QLabel,
                             QPushButton, QMessageBox, QWidget, QApplication)

from .plugin_manager import discover_plugins, get_plugin_meta, compare_versions
from .config import save_plugin_state, load_models, save_models, _new_model_id


def show_settings(window):
    """偏好设置：思考模式、工具调用、自主模式、主题。
    模型相关设置（Base URL / API Key / 模型 ID / 代理）请通过「菜单→模型」管理。
    工作目录已按对话/任务自动隔离（每个 talk_*/cron_* 独立目录），无需手动配置。
    """
    dlg = QDialog(window)
    dlg.setWindowTitle("偏好设置")
    dlg.resize(480, 280)
    layout = QFormLayout(dlg)

    # 顶部提示：引导模型设置走「菜单→模型」
    hint = QLabel("模型连接（URL / Key / 模型 ID / 代理）请通过「菜单→模型」管理。\n"
                  "工作目录已自动按对话/定时任务隔离（talk_*/cron_*），无需手动设置。")
    hint.setStyleSheet("color:#8A8F99;")
    hint.setWordWrap(True)
    layout.addRow(hint)

    chk_think = QCheckBox("启用思考模式 (enable_thinking)")
    chk_think.setChecked(window.enable_thinking)
    chk_tools = QCheckBox("启用工具调用 (计算器、时间查询等)")
    chk_tools.setChecked(window.enable_tools)
    chk_tools.setToolTip("需要模型支持 function calling / tool use 功能。\n如模型不支持，请关闭此选项。")

    # ── 自主 / Agent 模式 ──
    chk_agent = QCheckBox("自主模式 (提高工具循环轮次，支持多步任务编排)")
    chk_agent.setChecked(window.agent_mode)
    chk_agent.setToolTip("开启后，模型可执行更多轮工具调用（如先检索再执行再整理），"
                         "适合复杂自动化任务。注意会增加 token 消耗。")
    sp_rounds = QSpinBox()
    sp_rounds.setRange(3, 30)
    sp_rounds.setValue(window.max_agent_rounds)
    sp_rounds.setSuffix(" 轮")

    layout.addRow("", chk_think)
    layout.addRow("", chk_tools)
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

    new_agent = chk_agent.isChecked()
    new_rounds = sp_rounds.value()
    new_theme = "light" if le_theme.currentText() == "浅色" else "dark"

    theme_changed = new_theme != window.theme

    window.enable_thinking = chk_think.isChecked()
    window.enable_tools = chk_tools.isChecked()
    window.agent_mode = new_agent
    window.max_agent_rounds = new_rounds
    window.theme = new_theme
    window._save_settings()

    if theme_changed:
        window.apply_theme(rerender_chat=True)

    window.display_message("系统",
        f"偏好已更新 — 思考: {'开' if window.enable_thinking else '关'} | "
        f"工具: {'开' if window.enable_tools else '关'} | "
        f"主题: {le_theme.currentText()}",
        "system")


def _model_edit_form(window, dlg, model=None):
    """模型编辑表单（添加/编辑共用）。model 为 None 时新建空表单。

    返回 (form_widget, get_values_fn)；get_values() 校验并返回字段字典，
    校验失败弹提示并返回 None。
    """
    from PyQt5.QtWidgets import QFormLayout, QLineEdit, QCheckBox, QWidget

    form = QWidget(dlg)
    fl = QFormLayout(form)
    fl.setContentsMargins(0, 0, 0, 0)

    m = model or {}
    le_name = QLineEdit(m.get("name", ""))
    le_name.setPlaceholderText("如：工作模型（阿里云）")
    le_url = QLineEdit(m.get("base_url", ""))
    le_url.setPlaceholderText("https://api.openai.com/v1 或其他 OpenAI 兼容端点")
    le_key = QLineEdit(m.get("api_key", ""))
    le_key.setEchoMode(QLineEdit.Password)
    le_key.setPlaceholderText("sk-...")
    le_model = QLineEdit(m.get("model_id", ""))
    le_model.setPlaceholderText("qwen-max / gpt-4o / DeepSeek-R1-...")
    le_proxy = QLineEdit(m.get("proxy", ""))
    le_proxy.setPlaceholderText("http://127.0.0.1:7890（留空=直连）")
    chk_think = QCheckBox("启用思考模式 (enable_thinking)")
    chk_think.setChecked(bool(m.get("enable_thinking", False)))
    chk_tools = QCheckBox("启用工具调用 (enable_tools)")
    chk_tools.setChecked(bool(m.get("enable_tools", True)))

    fl.addRow("名称:", le_name)
    fl.addRow("Base URL:", le_url)
    fl.addRow("API Key:", le_key)
    fl.addRow("模型 ID:", le_model)
    fl.addRow("代理:", le_proxy)
    fl.addRow("", chk_think)
    fl.addRow("", chk_tools)

    def get_values():
        name = le_name.text().strip()
        url = le_url.text().strip()
        key = le_key.text().strip()
        mid = le_model.text().strip()
        proxy = le_proxy.text().strip()
        if not url or not mid:
            QMessageBox.warning(dlg, "提示", "Base URL 和 模型ID 不能为空")
            return None
        return {
            "name": name or mid,
            "base_url": url, "api_key": key, "model_id": mid, "proxy": proxy,
            "enable_thinking": chk_think.isChecked(),
            "enable_tools": chk_tools.isChecked(),
        }

    return form, get_values


def _test_model_connection(parent, values):
    """测试模型连接：发送一条最小 chat 请求，返回 (ok, 消息)"""
    from .config import make_openai_client
    try:
        client = make_openai_client(values["api_key"], values["base_url"], values["proxy"])
        resp = client.chat.completions.create(
            model=values["model_id"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
            timeout=20,
        )
        text = (resp.choices[0].message.content or "").strip()
        return True, f"连接成功，模型回复: {text[:50] or '(空)'}"
    except Exception as e:
        return False, f"连接失败: {e}"


def show_model_manager(window):
    """模型管理对话框 — 多模型增删改、切换、测试连接"""
    dlg = QDialog(window)
    dlg.setWindowTitle("模型管理")
    dlg.resize(600, 420)
    layout = QVBoxLayout(dlg)

    info = QLabel("保存多个 API 来源的模型，在「模型」菜单或此处切换当前使用的模型。")
    info.setWordWrap(True)
    layout.addWidget(info)

    list_widget = QListWidget()

    def refresh_list(current_id=None):
        list_widget.clear()
        models, cur = load_models()
        if current_id:
            cur = current_id
        for m in models:
            is_cur = m.get("id") == cur
            label = (f"{'[当前] ' if is_cur else ''}{m.get('name') or m.get('model_id')}"
                     f"  —  {m.get('model_id')} @ {m.get('base_url')}")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, m.get("id"))
            list_widget.addItem(item)
        return models, cur

    models, current_id = refresh_list()
    layout.addWidget(list_widget)

    def selected_id():
        item = list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    # ── 按钮行 ──
    btn_layout = QHBoxLayout()
    btn_add = QPushButton("➕ 添加")
    btn_edit = QPushButton("✏️ 编辑")
    btn_test = QPushButton("🔌 测试连接")
    btn_del = QPushButton("🗑 删除")
    btn_use = QPushButton("✅ 设为当前")
    for b in (btn_add, btn_edit, btn_test, btn_del, btn_use):
        b.setCursor(Qt.PointingHandCursor)
        btn_layout.addWidget(b)
    btn_layout.addStretch()
    layout.addLayout(btn_layout)

    def on_add():
        form, get_values = _model_edit_form(window, dlg)
        e = QDialog(dlg); e.setWindowTitle("添加模型"); e.resize(460, 300)
        v = QVBoxLayout(e); v.addWidget(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(e.accept); bb.rejected.connect(e.reject)
        v.addWidget(bb)
        if e.exec_() != QDialog.Accepted:
            return
        vals = get_values()
        if not vals:
            return
        models, cur = load_models()
        new_m = {"id": _new_model_id(), **vals}
        models.append(new_m)
        # 第一条模型自动设为当前
        cur = new_m["id"] if len(models) == 1 else cur
        save_models(models, cur)
        window.current_model_id = cur
        refresh_list(cur)

    def on_edit():
        mid = selected_id()
        if not mid:
            QMessageBox.information(dlg, "提示", "请先选择一个模型")
            return
        models, cur = load_models()
        target = next((m for m in models if m["id"] == mid), None)
        if not target:
            return
        form, get_values = _model_edit_form(window, dlg, target)
        e = QDialog(dlg); e.setWindowTitle("编辑模型"); e.resize(460, 300)
        v = QVBoxLayout(e); v.addWidget(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(e.accept); bb.rejected.connect(e.reject)
        v.addWidget(bb)
        if e.exec_() != QDialog.Accepted:
            return
        vals = get_values()
        if not vals:
            return
        target.update(vals)
        save_models(models, cur)
        # 编辑的是当前模型 → 同步激活字段并重建 client
        if mid == cur:
            _apply_to_window(window, target)
        refresh_list(cur)

    def on_test():
        mid = selected_id()
        if not mid:
            QMessageBox.information(dlg, "提示", "请先选择一个模型")
            return
        models, cur = load_models()
        target = next((m for m in models if m["id"] == mid), None)
        if not target:
            return
        btn_test.setText("测试中...")
        btn_test.setEnabled(False)
        QApplication.processEvents()
        ok, msg = _test_model_connection(dlg, target)
        btn_test.setText("🔌 测试连接")
        btn_test.setEnabled(True)
        (QMessageBox.information if ok else QMessageBox.warning)(dlg, "测试连接", msg)

    def on_del():
        mid = selected_id()
        if not mid:
            QMessageBox.information(dlg, "提示", "请先选择一个模型")
            return
        models, cur = load_models()
        if len(models) <= 1:
            QMessageBox.warning(dlg, "提示", "至少保留一个模型，不能删除")
            return
        target = next((m for m in models if m["id"] == mid), None)
        name = target.get("name") or target.get("model_id") if target else mid
        if QMessageBox.question(dlg, "确认删除", f"确定删除模型「{name}」？") != QMessageBox.Yes:
            return
        models = [m for m in models if m["id"] != mid]
        if cur == mid:
            cur = models[0]["id"]
            _apply_to_window(window, models[0])
        save_models(models, cur)
        window.current_model_id = cur
        refresh_list(cur)

    def on_use():
        mid = selected_id()
        if not mid:
            QMessageBox.information(dlg, "提示", "请先选择一个模型")
            return
        models, cur = load_models()
        target = next((m for m in models if m["id"] == mid), None)
        if not target:
            return
        if mid != cur:
            save_models(models, mid)
            window.switch_model(mid)
        refresh_list(mid)

    btn_add.clicked.connect(on_add)
    btn_edit.clicked.connect(on_edit)
    btn_test.clicked.connect(on_test)
    btn_del.clicked.connect(on_del)
    btn_use.clicked.connect(on_use)

    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    buttons.clicked.connect(lambda *_: dlg.close())
    layout.addWidget(buttons)

    dlg.exec_()


def _apply_to_window(window, model):
    """把模型字段同步到 window 激活字段并重建 client（编辑/删除当前模型时）"""
    window.base_url = model.get("base_url", "")
    window.api_key = model.get("api_key", "")
    window.model_id = model.get("model_id", "")
    window.proxy = model.get("proxy", "")
    window.enable_thinking = bool(model.get("enable_thinking", False))
    window.enable_tools = bool(model.get("enable_tools", True))
    window.setup_client()
    window._save_settings()


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
