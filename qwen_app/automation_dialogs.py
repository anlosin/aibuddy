"""自动化任务相关对话框（从 chat_window.py 拆分）。

包含：
- AutomationManagerDialog：任务列表（新建/编辑/删除/立即运行/查看日志/刷新）
  使用表格展示，支持内联启用开关、彩色状态徽标、按状态/名称筛选、显示下次执行时间。
- AutomationEditDialog：单个任务的编辑表单
- LogViewDialog：任务历史执行记录查看
  支持按结果筛选、关键词搜索、概览统计、复制全文、Markdown 高亮。

这些对话框仅通过 parent.scheduler 访问调度器，与 ChatWindow 其它状态解耦。
"""

import threading
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal, QDateTime, QTime, QRegExp
from PyQt5.QtGui import QColor, QTextCharFormat, QFont, QSyntaxHighlighter
from PyQt5.QtWidgets import (QDialog, QWidget, QTableWidget, QTableWidgetItem,
                             QListWidget, QListWidgetItem, QPushButton, QLabel, QMessageBox,
                             QFormLayout, QLineEdit, QTextEdit, QCheckBox,
                             QSpinBox, QComboBox, QGroupBox, QHBoxLayout,
                             QVBoxLayout, QDateTimeEdit, QTimeEdit,
                             QDialogButtonBox, QHeaderView, QAbstractItemView)

from .scheduler import describe_schedule, next_run_time
from .config import load_models


# ── 状态配色 ──
_STATUS_OK = QColor("#2E7D32")      # 绿：成功
_STATUS_ERROR = QColor("#C62828")   # 红：失败
_STATUS_UNKNOWN = QColor("#9E9E9E") # 灰：未运行
_STATUS_DISABLED = QColor("#BDBDBD")# 浅灰：禁用
_ACCENT = QColor("#3B6CF6")         # 主蓝


class MarkdownLogHighlighter(QSyntaxHighlighter):
    """轻量 Markdown 高亮，仅用于执行记录展示（标题/工具调用/状态行上色）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fmts = []
        # 一级标题
        f = QTextCharFormat()
        f.setForeground(QColor("#1A56DB"))
        f.setFontWeight(QFont.Bold)
        self._fmts.append((QRegExp(r"^# .*"), f))
        # 二级标题
        f = QTextCharFormat()
        f.setForeground(QColor("#2563EB"))
        f.setFontWeight(QFont.Bold)
        self._fmts.append((QRegExp(r"^## .*"), f))
        # 三级标题（工具调用名）
        f = QTextCharFormat()
        f.setForeground(QColor("#7C3AED"))
        self._fmts.append((QRegExp(r"^### .*"), f))
        # 状态行
        f = QTextCharFormat()
        f.setForeground(QColor("#B45309"))
        self._fmts.append((QRegExp(r"^- \*\*状态\*\*.*"), f))

    def highlightBlock(self, text):
        for rx, fmt in self._fmts:
            rx.setMinimal(False)
            idx = rx.indexIn(text)
            while idx >= 0:
                self.setFormat(idx, rx.matchedLength(), fmt)
                idx = rx.indexIn(text, idx + rx.matchedLength())


class AutomationManagerDialog(QDialog):
    """自动化任务列表：新建 / 编辑 / 删除 / 立即运行 / 查看日志 / 刷新

    表格化展示，每行可内联切换启用状态；状态用彩色徽标；支持按状态/名称筛选。
    """

    # 跨线程安全回调：worker 线程执行完后用信号把结果发回 GUI 线程
    _run_done = pyqtSignal(str, str)  # (final, error)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._run_done.connect(self._on_done)
        self.setWindowTitle("自动化任务管理")
        self.resize(880, 540)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── 标题行 ──
        title = QLabel("自动化任务")
        title.setStyleSheet("font-size:15px;font-weight:bold;color:#1F2329;")
        layout.addWidget(title)

        # ── 工具栏：筛选 + 计数 ──
        bar = QHBoxLayout()
        bar.addWidget(QLabel("筛选:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", "已启用", "已禁用", "有错误"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索任务名称…")
        self.search_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search_edit, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#8A8F99;")
        bar.addWidget(self.count_label)
        layout.addLayout(bar)

        # ── 任务表格 ──
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["任务名称", "周期", "模型", "下次执行", "上次状态", "启用"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        h_header = self.table.horizontalHeader()
        assert h_header is not None
        h_header.setSectionResizeMode(0, QHeaderView.Stretch)
        h_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        h_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._edit_selected)
        layout.addWidget(self.table, 1)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        self.btn_new = QPushButton("＋ 新建")
        self.btn_edit = QPushButton("编辑")
        self.btn_del = QPushButton("删除")
        self.btn_run = QPushButton("▶ 立即运行")
        self.btn_log = QPushButton("📋 查看日志")
        self.btn_refresh = QPushButton("↻ 刷新")
        for b in (self.btn_new, self.btn_edit, self.btn_del,
                  self.btn_run, self.btn_log, self.btn_refresh):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#8A8F99;")
        layout.addWidget(self.status)

        self.btn_new.clicked.connect(self._new)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_del.clicked.connect(self._delete)
        self.btn_run.clicked.connect(self._run_now)
        self.btn_log.clicked.connect(self._view_logs)
        self.btn_refresh.clicked.connect(self.refresh)

        # 单击「启用」列(第5列)切换启用状态：只连一次
        self.table.cellClicked.connect(self._on_cell_clicked)

        self.refresh()

    # ────────────────────────────────
    def _items(self):
        return self.parent_window.scheduler.automations

    def refresh(self):
        # 先记住当前选中的 id，刷新后恢复
        prev = self._selected_id()
        self.table.setRowCount(0)
        now = datetime.now()
        for a in self._items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            aid = a.get("id", "")

            # 名称（含上次错误提示）；UserRole 存 id 供反查
            err = a.get("last_error", "")
            name_item = QTableWidgetItem(a.get("name", "?"))
            name_item.setData(Qt.UserRole, aid)
            if err:
                name_item.setToolTip(f"上次错误: {err}")
            self.table.setItem(row, 0, name_item)

            # 周期
            self.table.setItem(row, 1, QTableWidgetItem(
                describe_schedule(a.get("schedule", {}))))

            # 执行模型（空 = 跟随主模型；显示注册表中的模型名）
            mid = a.get("model_id", "")
            if mid:
                models, _ = load_models()
                m = next((x for x in models if x.get("id") == mid), None)
                model_txt = (m.get("name") or m.get("model_id", mid)) if m \
                    else f"(已删除 {mid})"
                model_color = _STATUS_UNKNOWN if not m else _ACCENT
            else:
                model_txt = "跟随主模型"
                model_color = _STATUS_UNKNOWN
            model_item = QTableWidgetItem(model_txt)
            model_item.setForeground(model_color)
            self.table.setItem(row, 2, model_item)

            # 下次执行
            if a.get("enabled", True):
                nxt = next_run_time(a, now)
                nxt_txt = nxt.strftime("%m-%d %H:%M") if nxt else "—"
            else:
                nxt_txt = "（已禁用）"
            self.table.setItem(row, 3, QTableWidgetItem(nxt_txt))

            # 上次状态（彩色徽标）
            status = a.get("last_status")
            enabled = a.get("enabled", True)
            if not enabled:
                mark, color = "○ 已禁用", _STATUS_DISABLED
            elif status == "ok":
                mark, color = "✓ 成功", _STATUS_OK
            elif status == "error":
                mark, color = "✗ 失败", _STATUS_ERROR
            else:
                mark, color = "? 未运行", _STATUS_UNKNOWN
            st_item = QTableWidgetItem(mark)
            st_item.setForeground(color)
            st_item.setFont(QFont("", -1, QFont.Bold))
            self.table.setItem(row, 4, st_item)

            # 启用开关（内联复选框）
            chk = QTableWidgetItem()
            chk.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            chk.setTextAlignment(Qt.AlignCenter)
            chk.setData(Qt.UserRole, aid)
            self.table.setItem(row, 5, chk)

        self.table.resizeColumnToContents(3)
        self._apply_filter()
        if prev:
            self._select_by_id(prev)

    # ── 启用开关：单击第 6 列切换 ──
    def _on_cell_clicked(self, row, col):
        if col != 5:
            return
        item = self.table.item(row, col)
        if not item:
            return
        aid = item.data(Qt.UserRole)
        new_state = item.checkState() != Qt.Checked
        item.setCheckState(Qt.Checked if new_state else Qt.Unchecked)
        self.parent_window.scheduler.set_enabled(aid, new_state)
        self.status.setText(f"已{'启用' if new_state else '禁用'}任务")
        self.refresh()

    def _apply_filter(self):
        text = self.search_edit.text().strip().lower()
        fmode = self.filter_combo.currentText()
        shown = 0
        for row in range(self.table.rowCount()):
            a = self._auto_at_row(row)
            if not a:
                continue
            enabled = a.get("enabled", True)
            status = a.get("last_status")
            ok = True
            if fmode == "已启用" and not enabled:
                ok = False
            elif fmode == "已禁用" and enabled:
                ok = False
            elif fmode == "有错误" and status != "error":
                ok = False
            if ok and text and text not in a.get("name", "").lower():
                ok = False
            self.table.setRowHidden(row, not ok)
            if ok:
                shown += 1
        total = self.table.rowCount()
        self.count_label.setText(f"显示 {shown} / {total} 个任务")

    def _auto_at_row(self, row):
        item = self.table.item(row, 0)
        if not item:
            return None
        # 通过行数据反查：用名称不可靠，改用 UserRole 存 id（名称列也存一份）
        aid = item.data(Qt.UserRole)
        if not aid:
            return None
        return next((a for a in self._items() if a.get("id") == aid), None)

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _select_by_id(self, aid):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == aid:
                self.table.selectRow(row)
                return

    def _new(self):
        dlg = AutomationEditDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.parent_window.scheduler.add_automation(
                name=dlg.name_edit.text().strip(),
                prompt=dlg.prompt_edit.toPlainText().strip(),
                schedule=dlg.get_schedule(),
                enabled=dlg.enabled_chk.isChecked(),
                max_rounds=dlg.rounds_spin.value(),
                model_id=dlg.model_combo.currentData() or "",
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
                model_id=dlg.model_combo.currentData() or "",
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
            # 用信号把结果安全送回 GUI 线程（worker 线程里不能操作 UI）
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
            QMessageBox.information(self, "提示", "请先选择一个任务")
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

        # ── 执行模型选择：首项「跟随主模型」，其余来自模型注册表 ──
        self.model_combo = QComboBox()
        self._model_ids = [""]  # 索引 0 恒为空 = 跟随主模型
        self.model_combo.addItem("跟随主模型", "")
        try:
            models, _ = load_models()
        except Exception:
            models = []
        for m in models:
            label = m.get("name") or m.get("model_id", "(未命名)")
            if m.get("model_id"):
                label += f" ({m['model_id']})"
            self._model_ids.append(m.get("id", ""))
            self.model_combo.addItem(label, m.get("id", ""))
        # 默认「跟随主模型」——运行时动态解析主模型，主模型切换后自动跟随；
        # 用户可手动改选注册表中的固定模型。
        self.model_combo.setCurrentIndex(0)

        layout.addRow("任务名称:", self.name_edit)
        layout.addRow("任务提示词:", self.prompt_edit)
        layout.addRow("执行模型:", self.model_combo)
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

    @staticmethod
    def _row_widget(widgets):
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
        # 恢复任务指定的执行模型（无该字段或为空 = 跟随主模型）
        idx = self.model_combo.findData(auto.get("model_id", ""))
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 0)
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
    """查看某个任务的历史执行记录，支持筛选 / 搜索 / 概览 / 复制 / 高亮。

    左：执行记录列表（状态、时间、耗时、字数、工具次数）；右：记录正文。
    """

    def __init__(self, parent=None, auto=None, scheduler=None):
        super().__init__(parent)
        self.auto = auto
        self.scheduler = scheduler
        self._all_runs = []
        self.setWindowTitle(
            f"执行记录 — {auto.get('name', '?')}" if auto else "执行记录")
        self.resize(920, 560)
        layout = QHBoxLayout(self)

        # ── 左侧：列表 + 筛选 ──
        left = QVBoxLayout()
        filter_bar = QHBoxLayout()
        self.result_combo = QComboBox()
        self.result_combo.addItems(["全部", "成功", "失败"])
        self.result_combo.currentTextChanged.connect(self._apply_log_filter)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索日志内容…")
        self.search_edit.textChanged.connect(self._apply_log_filter)
        filter_bar.addWidget(QLabel("结果:"))
        filter_bar.addWidget(self.result_combo)
        filter_bar.addWidget(self.search_edit, 1)
        left.addLayout(filter_bar)

        self.list = QListWidget()
        self.list.itemClicked.connect(self._show_current)
        self.list.itemDoubleClicked.connect(self._show_current)
        left.addWidget(self.list, 1)

        self.overview = QLabel("")
        self.overview.setStyleSheet("color:#8A8F99;")
        left.addWidget(self.overview)
        layout.addLayout(left, 1)

        # ── 右侧：正文 ──
        right = QVBoxLayout()
        right_bar = QHBoxLayout()
        self.copy_btn = QPushButton("📄 复制全文")
        self.copy_btn.clicked.connect(self._copy_current)
        right_bar.addStretch()
        right_bar.addWidget(self.copy_btn)
        right.addLayout(right_bar)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.NoWrap)
        self._hl = MarkdownLogHighlighter(self.view.document())
        right.addWidget(self.view, 1)
        layout.addLayout(right, 2)

        if auto and scheduler:
            self._all_runs = scheduler.list_runs(auto.get("id"))
            self._fill_list(self._all_runs)
            self._update_overview()
        if self.list.count():
            self.list.setCurrentRow(0)
            self._show_current()

    def _fill_list(self, runs):
        self.list.clear()
        for r in runs:
            started = r.get("started_at", "")[:16]
            mark = "✓" if r.get("status") == "ok" else "✗"
            dur = self._duration(r)
            item = QListWidgetItem(
                f"{mark} {started}  ({dur}s / "
                f"{r.get('output_chars', 0)}字 / "
                f"{r.get('tool_calls', 0)}次工具)")
            item.setData(Qt.UserRole, r.get("run_id"))
            self.list.addItem(item)

    @staticmethod
    def _duration(r):
        try:
            s = datetime.fromisoformat(r.get("started_at", ""))
            f = datetime.fromisoformat(r.get("finished_at", ""))
            return round((f - s).total_seconds(), 1)
        except (ValueError, AttributeError, TypeError):
            return 0

    def _update_overview(self):
        runs = self._all_runs
        if not runs:
            self.overview.setText("暂无执行记录")
            return
        ok = sum(1 for r in runs if r.get("status") == "ok")
        err = len(runs) - ok
        chars = sum(r.get("output_chars", 0) for r in runs)
        avg = sum(self._duration(r) for r in runs) / len(runs)
        self.overview.setText(
            f"共 {len(runs)} 次 · 成功 {ok} · 失败 {err} · "
            f"总 {chars} 字 · 平均耗时 {avg:.1f}s")

    def _apply_log_filter(self):
        mode = self.result_combo.currentText()
        kw = self.search_edit.text().strip().lower()
        filtered = []
        for r in self._all_runs:
            if mode == "成功" and r.get("status") != "ok":
                continue
            if mode == "失败" and r.get("status") != "error":
                continue
            if kw:
                # 仅对内容做轻量匹配（避免每次读全文，先按索引字段粗筛）
                hay = (r.get("name", "") + r.get("error", "")).lower()
                if kw not in hay:
                    content = (self.scheduler.read_log(r.get("run_id")) or "")
                    if kw not in content.lower():
                        continue
            filtered.append(r)
        self._fill_list(filtered)
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
        # 回到顶部
        self.view.moveCursor(self.view.textCursor().Start)

    def _copy_current(self):
        text = self.view.toPlainText()
        if not text:
            return
        cb = self.view.clipboard()
        cb.setText(text)
        QMessageBox.information(self, "已复制", "当前执行记录全文已复制到剪贴板")
