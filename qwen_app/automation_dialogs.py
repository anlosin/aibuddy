"""自动化任务相关对话框（从 chat_window.py 拆分）。

包含：
- AutomationManagerDialog：任务列表（新建/编辑/删除/立即运行/查看日志/刷新）
- AutomationEditDialog：单个任务的编辑表单
- LogViewDialog：任务历史执行记录查看

这些对话框仅通过 parent.scheduler 访问调度器，与 ChatWindow 其它状态解耦。
"""

import threading

from PyQt5.QtCore import Qt, pyqtSignal, QDateTime, QTime
from PyQt5.QtWidgets import (QDialog, QWidget, QListWidget, QListWidgetItem,
                             QPushButton, QLabel, QMessageBox, QFormLayout,
                             QLineEdit, QTextEdit, QCheckBox, QSpinBox,
                             QComboBox, QGroupBox, QHBoxLayout, QVBoxLayout,
                             QDateTimeEdit, QTimeEdit, QDialogButtonBox)

from .scheduler import describe_schedule


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
