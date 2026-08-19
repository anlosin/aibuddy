"""会话管理（从 chat_window.py 拆分）。

提供会话的增删改查、列表刷新、历史加载/保存等纯逻辑函数。所有函数以
window（ChatWindow 实例）为首个参数，通过 window 访问其状态字段，互相
调用时直接引用同模块内的函数名。
"""

import os
import json
import uuid
from datetime import datetime

from PyQt5.QtCore import Qt, QPoint, QSize, QTimer
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QLabel, QPushButton,
                             QListWidgetItem, QMenu, QInputDialog,
                             QMessageBox, QSizePolicy, QApplication,
                             QFileDialog, QStyle)

from .theme import get_palette
from .config import load_conversations, save_conversations, CONVERSATIONS_DIR


def get_current_conv(window):
    for c in window.conversations:
        if c["id"] == window.current_conv_id:
            return c
    return None


def new_conversation(window):
    conv = {
        "id": str(uuid.uuid4())[:8],
        "title": "新对话",
        "history": [],
        "created_at": datetime.now().isoformat(),
    }
    window.conversations.insert(0, conv)
    window.current_conv_id = conv["id"]
    save_convs(window)
    refresh_conv_list(window)
    load_current_conv(window)
    window.input_field.setFocus()


def on_conv_selected(window, item):
    conv_id = item.data(Qt.UserRole)
    if conv_id == window.current_conv_id:
        return
    save_current_to_conv(window)
    window.current_conv_id = conv_id
    load_current_conv(window)
    refresh_conv_list(window)


def _relative_time(iso_str):
    """把 ISO 时间转成类似 QQ 列表的相对时间。"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
    except Exception:
        return ""
    seconds = int((datetime.now() - dt).total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    if seconds < 7 * 86400:
        return f"{seconds // 86400}天前"
    return dt.strftime("%m-%d")


def refresh_conv_list(window):
    pal = get_palette(window.theme)
    window.conv_list.clear()

    # 预计算尺寸：保证 icon / title / time / menu 按钮在一行内
    # 用 style 的滚动条宽度指标，无论滚动条当前是否显示都预留正确空间
    sb_w = window.style().pixelMetric(QStyle.PM_ScrollBarExtent)
    vp = window.conv_list.viewport().width()
    avail = vp if vp and vp > 0 else window.conv_list.width()
    icon_w, time_w, btn_w = 20, 66, 22
    h_margin, v_margin, spacing = 8, 6, 4
    reserved = h_margin + (sb_w + 4) + icon_w + spacing + time_w + spacing + btn_w + spacing
    max_title_w = max(40, avail - reserved) if avail and avail > 0 else 120

    for conv in window.conversations:
        cid = conv["id"]
        title = conv.get("title", "新对话") or "新对话"
        selected = cid == window.current_conv_id

        row_widget = QWidget()
        row_widget.setObjectName(f"convRow_{cid}")
        # 注意：QListView 在 list 模式下会把 item widget 拉伸到"内容宽"
        # （= 列表宽 - 边框，含纵向滚动条占位），sizeHint 宽度不生效。
        # 因此右侧必须预留滚动条宽度，否则 ⋯ 按钮右缘会被视口裁掉、点不到。
        row_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row_widget.setMinimumWidth(0)
        bg_color = pal["conv_sel"] if selected else "transparent"
        hover_color = pal["conv_sel"] if selected else pal["conv_hover"]
        row_widget.setStyleSheet(
            f"QWidget#{row_widget.objectName()} {{ background-color: {bg_color}; border-radius: 6px; }}"
            f"QWidget#{row_widget.objectName()}:hover {{ background-color: {hover_color}; }}"
        )

        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(h_margin, v_margin, sb_w + 4, v_margin)
        row_layout.setSpacing(spacing)

        # 左侧小图标
        icon = QLabel("💬")
        icon.setFixedSize(icon_w, icon_w)
        icon.setStyleSheet(f"background: transparent; font-size: 13px; color: {pal['conv_fg']};")
        icon.setAlignment(Qt.AlignCenter)
        row_layout.addWidget(icon)

        # 会话标题（单行省略）
        label = QLabel()
        label.setObjectName("convTitle")
        label.setStyleSheet(f"background: transparent; font-size: 13px; color: {pal['conv_fg']};")
        label.setCursor(Qt.PointingHandCursor)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        label.setMinimumWidth(0)
        label.setToolTip(title)
        flat_title = " ".join(str(title).split())
        fm = label.fontMetrics()
        elided = fm.elidedText(flat_title, Qt.ElideRight, max_title_w)
        label.setFixedWidth(max_title_w)
        label.setText(elided)
        row_layout.addWidget(label, 1)

        # 右侧相对时间
        time_label = QLabel(_relative_time(conv.get("created_at")))
        time_label.setObjectName("convTime")
        time_label.setFixedWidth(time_w)
        time_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        time_label.setStyleSheet(
            f"background: transparent; font-size: 11px; color: {pal['conv_time']};"
        )
        time_label.setToolTip(str(conv.get("created_at", "")))
        row_layout.addWidget(time_label)

        # 更多操作按钮：默认透明隐藏，hover 行时才显示
        btn = QLabel("⋯")
        btn.setObjectName("btnConvMenu")
        btn.setAlignment(Qt.AlignCenter)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(btn_w, btn_w)
        transparent_ss = (
            f"QLabel#btnConvMenu {{ background: transparent; color: transparent;"
            f" font-size: 14px; border-radius: 4px; }}"
        )
        visible_ss = (
            f"QLabel#btnConvMenu {{ color: {pal['conv_fg']};"
            f" background-color: {pal['conv_hover']}; font-size: 14px;"
            f" border-radius: 4px; }}"
        )
        btn.setStyleSheet(transparent_ss)
        btn.mousePressEvent = lambda e, cid=cid, title=title, btn=btn: show_conv_menu(window, cid, title, btn)
        row_layout.addWidget(btn)

        # 行 hover 时显示菜单按钮；鼠标移出（且不在按钮上）时隐藏
        def _make_enter(b, vs):
            def _enter(_e):
                b.setStyleSheet(vs)
            return _enter

        def _make_leave(rw, b, ts):
            def _leave(_e):
                pos = rw.mapFromGlobal(QCursor.pos())
                if not rw.rect().contains(pos):
                    b.setStyleSheet(ts)
            return _leave

        row_widget.enterEvent = _make_enter(btn, visible_ss)
        row_widget.leaveEvent = _make_leave(row_widget, btn, transparent_ss)

        # 点击图标 / 标题 / 时间均可切换会话
        for w in (icon, label, time_label):
            w.mousePressEvent = lambda e, cid=cid: select_conv_by_id(window, cid)

        item = QListWidgetItem()
        item.setData(Qt.UserRole, cid)
        # 只取行高；宽度锁到视口，避免 item 自然宽度撑出横向滚动条。
        # 兜底关键：setup_ui 阶段窗口未 show，viewport 可能是 0 或默认大宽度，
        # 若把 item 锁成 0/过窄，行被压扁、⋯ 按钮被裁出视口 → 点不到。
        rh = row_widget.sizeHint()
        vp_w = window.conv_list.viewport().width()
        list_w = window.conv_list.width()
        item_w = vp_w if vp_w and vp_w > 0 else (list_w if list_w and list_w > 0 else 240)
        item_w = max(item_w, 80)  # 仅防 0/极小值；有真实视口宽时优先用真实值
        # 强制行 widget 宽度与 item 一致，QListWidget 在部分环境下不会自动拉伸 widget
        row_widget.setFixedWidth(item_w)
        item.setSizeHint(QSize(item_w, rh.height()))
        window._conv_vp_w = vp_w  # 记住本次视口宽，供 resize 过滤器判断是否需要重建
        window.conv_list.addItem(item)
        window.conv_list.setItemWidget(item, row_widget)
        if selected:
            item.setSelected(True)


def load_current_conv(window):
    conv = get_current_conv(window)
    window.chat_display.clear()
    window.current_tag = None
    window.conversation_history = []
    if not conv:
        return
    window.conversation_history = list(conv.get("history", []))
    for msg in window.conversation_history:
        role = "您" if msg["role"] == "user" else window.model_id
        tag = "user" if msg["role"] == "user" else "ai"
        window.display_message(role, msg["content"], tag, msg.get("time"))
    window.update_status()


def save_current_to_conv(window):
    conv = get_current_conv(window)
    if conv:
        conv["history"] = list(window.conversation_history)
        if not conv.get("title") or conv["title"] == "新对话":
            for m in window.conversation_history:
                if m["role"] == "user":
                    # 只取第一行并压缩空白，避免多行长问题把标题撑成多行
                    first_line = m["content"].split("\n", 1)[0].strip()
                    if not first_line:
                        first_line = m["content"].strip()
                    conv["title"] = first_line[:30] + ("..." if len(first_line) > 30 else "")
                    break
        save_convs(window)
        refresh_conv_list(window)


def load_convs(window):
    window.conversations, window.current_conv_id = load_conversations()


def save_convs(window):
    save_conversations(window.conversations, window.current_conv_id)


def select_conv_by_id(window, conv_id):
    """点击会话标签时切换对话"""
    if conv_id == window.current_conv_id:
        return
    save_current_to_conv(window)
    window.current_conv_id = conv_id
    load_current_conv(window)
    refresh_conv_list(window)


def _open_storage_location():
    """打开会话数据所在文件夹。"""
    if os.name == "nt":
        os.startfile(CONVERSATIONS_DIR)
    else:
        import subprocess
        subprocess.call(["xdg-open", CONVERSATIONS_DIR])


def _export_conversation(window, conv_id):
    """导出单个会意为 JSON 文件。"""
    conv = next((c for c in window.conversations if c["id"] == conv_id), None)
    if not conv:
        return
    default_name = f"conversation_{conv_id}.json"
    path, _ = QFileDialog.getSaveFileName(
        window, "导出会话", default_name, "JSON (*.json)"
    )
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conv, f, ensure_ascii=False, indent=2)


def _copy_title(window, title):
    """把会话标题复制到剪贴板。"""
    app = QApplication.instance()
    if app:
        app.clipboard().setText(title)
        if getattr(window, "status_label", None):
            window.status_label.setText("已复制会话标题")
            QTimer.singleShot(1500, window.update_status)


def show_conv_menu(window, conv_id, title, button):
    """显示会话的 ⋯ 菜单（QQ / 工作台风格）。"""
    pal = get_palette(window.theme)
    menu = QMenu(window)
    menu.setStyleSheet(f"""
        QMenu {{
            background-color: {pal['ai_bubble']};
            border: 1px solid {pal['conv_hover']};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 8px 24px;
            font-size: 13px;
            color: {pal['conv_fg']};
        }}
        QMenu::item:selected {{
            background-color: {pal['conv_sel']};
            border-radius: 4px;
        }}
        QMenu::item:disabled {{
            color: {pal['conv_time']};
        }}
        QMenu::separator {{
            background: {pal['conv_hover']};
            height: 1px;
            margin: 4px 8px;
        }}
    """)

    act_batch = menu.addAction("☰  批量操作")
    act_batch.setEnabled(False)
    act_open = menu.addAction("📂  打开存储位置")
    act_rename = menu.addAction("✏  重命名")
    menu.addSeparator()
    act_save = menu.addAction("💾  导出会话")
    act_share = menu.addAction("↗  复制标题")
    menu.addSeparator()
    act_delete = menu.addAction("🗑  删除会话")
    act_delete.setData(conv_id)

    chosen = menu.exec_(button.mapToGlobal(QPoint(0, button.height())))

    if chosen == act_rename:
        rename_conversation(window, conv_id, title)
    elif chosen == act_delete:
        delete_conversation(window, conv_id)
    elif chosen == act_open:
        _open_storage_location()
    elif chosen == act_save:
        _export_conversation(window, conv_id)
    elif chosen == act_share:
        _copy_title(window, title)


def rename_conversation(window, conv_id, old_title):
    """重命名会话"""
    new_title, ok = QInputDialog.getText(
        window, "重命名会话", "请输入新名称：",
        text=old_title
    )
    if ok and new_title.strip():
        clean_title = " ".join(new_title.split())
        for conv in window.conversations:
            if conv["id"] == conv_id:
                conv["title"] = clean_title
                break
        save_convs(window)
        refresh_conv_list(window)


def delete_conversation(window, conv_id):
    """删除会话"""
    reply = QMessageBox.question(
        window, "确认", "确定要删除这个会话吗？\n此操作不可撤销。",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
    )
    if reply != QMessageBox.Yes:
        return
    window.conversations = [c for c in window.conversations if c["id"] != conv_id]
    # 如果删的是当前对话，切换到第一个
    if conv_id == window.current_conv_id:
        if window.conversations:
            window.current_conv_id = window.conversations[0]["id"]
        else:
            new_conversation(window)
            return
    save_convs(window)
    load_current_conv(window)
    refresh_conv_list(window)
    window.display_message("系统", "会话已删除", "system")
