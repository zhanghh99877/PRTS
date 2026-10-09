"""触发器页：任务表 + 新建/编辑对话框 + 日志查看。"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, Qt, QTime, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from core.scheduler import MAIN_LOG, Scheduler, TaskStore
from ui import theme as theme_mod

_TYPE_NAMES = {"script": "Python 脚本", "yuflow": "YuFlow 工作流"}
_STATUS_NAMES = {"ok": "成功", "failed": "失败", "stopped": "已停止",
                 "running": "运行中", "cancelled": "已取消", None: "-"}


class TimeListEditor(QWidget):
    """多个时间点编辑：每行一个 QTimeEdit + ×，底部 ＋ 添加。

    值格式 "HH:mm,HH:mm"（去重、升序），与 tasks.sched_param 的 daily 约定一致。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)
        add = QPushButton("＋")
        add.setFixedWidth(28)
        add.setProperty("class", "icon")
        add.setToolTip("添加一个时间")
        add.clicked.connect(lambda: self._add_row(QTime(8, 0)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._rows)
        layout.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)
        self._add_row(QTime(8, 0))  # 默认一行

    def set_values(self, s: str):
        while self._rows.count():
            item = self._rows.takeAt(0)
            item.widget().deleteLater()
        times = [t.strip() for t in (s or "").split(",") if t.strip()]
        for t in times or ["08:00"]:
            self._add_row(QTime.fromString(t, "HH:mm"))

    def values(self) -> str:
        out = set()
        for i in range(self._rows.count()):
            te = self._rows.itemAt(i).widget().findChild(QTimeEdit)
            out.add(te.time().toString("HH:mm"))
        return ",".join(sorted(out))

    def _add_row(self, t: QTime):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        te = QTimeEdit(t if t.isValid() else QTime(8, 0))
        te.setDisplayFormat("HH:mm")
        rm = QPushButton("×")
        rm.setFixedWidth(24)
        rm.setProperty("class", "icon")
        rm.setToolTip("移除这个时间")
        rm.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(te, 1)
        h.addWidget(rm)
        self._rows.addWidget(row)

    def _remove_row(self, row: QWidget):
        if self._rows.count() <= 1:
            return  # 至少保留一行
        self._rows.removeWidget(row)
        row.deleteLater()


_WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]  # 索引 = Python weekday()


class TaskDialog(QDialog):
    """新建 / 编辑任务。"""

    def __init__(self, parent=None, task: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑任务" if task else "新建任务")
        self.setMinimumWidth(480)

        self.name_edit = QLineEdit()
        self.type_combo = QComboBox()
        self.type_combo.addItem("Python 脚本", "script")
        self.type_combo.addItem("YuFlow 工作流", "yuflow")

        self.target_edit = QLineEdit()
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_edit, 1)
        target_row.addWidget(browse)

        self.sched_combo = QComboBox()
        self.sched_combo.addItem("每 N 分钟", "interval")
        self.sched_combo.addItem("每天定时", "daily")
        self.sched_combo.addItem("仅一次", "once")
        self.sched_combo.addItem("手动触发", "manual")
        self.interval_spin = QSpinBox(minimum=1, maximum=24 * 60, value=30)
        self.interval_spin.setSuffix(" 分钟")
        # daily 页：多个时间 + 星期几限定
        self.time_list = TimeListEditor()
        self.weekday_checks: list[QCheckBox] = []
        wd_row = QHBoxLayout()
        wd_row.addWidget(QLabel("星期"))
        for name in _WEEKDAY_NAMES:
            c = QCheckBox(name)
            c.setChecked(True)
            self.weekday_checks.append(c)
            wd_row.addWidget(c)
        wd_row.addStretch(1)
        daily_page = QWidget()
        daily_layout = QVBoxLayout(daily_page)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        daily_layout.addWidget(self.time_list)
        daily_layout.addLayout(wd_row)
        self.once_dt = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.once_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.once_dt.setCalendarPopup(True)
        manual_hint = QLabel("只能通过「立即运行」手动触发")
        manual_hint.setStyleSheet("color: #888;")
        self.param_stack = QStackedWidget()
        for w in (self.interval_spin, daily_page, self.once_dt, manual_hint):
            self.param_stack.addWidget(w)
        self.sched_combo.currentIndexChanged.connect(self.param_stack.setCurrentIndex)

        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        self.catchup_check = QCheckBox("过期补跑（错过的触发在启动后补跑；仅作用于仅一次/每天定时）")
        self.catchup_check.setChecked(True)
        # 补跑时限：0 = 不限（超时则 once 视为错过、daily 跳过该时间点）
        self.catchup_window_spin = QSpinBox(minimum=0, maximum=7 * 24 * 60, value=0)
        self.catchup_window_spin.setSuffix(" 分钟")
        self.catchup_window_spin.setSpecialValueText("不限")
        self.catchup_check.toggled.connect(self.catchup_window_spin.setEnabled)
        catchup_row = QWidget()
        cr = QHBoxLayout(catchup_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(self.catchup_check)
        cr.addWidget(QLabel("时限"))
        cr.addWidget(self.catchup_window_spin)
        cr.addStretch(1)

        form = QFormLayout()
        form.addRow("名称", self.name_edit)
        form.addRow("类型", self.type_combo)
        form.addRow("目标", target_row)
        form.addRow("规则", self.sched_combo)
        form.addRow("参数", self.param_stack)
        form.addRow("", self.enabled_check)
        form.addRow("", catchup_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if task:
            self.name_edit.setText(task["name"])
            self.type_combo.setCurrentIndex(self.type_combo.findData(task["type"]))
            self.target_edit.setText(task["target"])
            self.sched_combo.setCurrentIndex(
                self.sched_combo.findData(task["sched_kind"]))
            if task["sched_kind"] == "interval":
                self.interval_spin.setValue(int(task["sched_param"]))
            elif task["sched_kind"] == "daily":
                self.time_list.set_values(task["sched_param"])
                wd = (task.get("weekdays") or "").strip()
                # 空 = 每天（全勾）；否则只勾列出的
                if wd:
                    allowed = {int(x) for x in wd.split(",")}
                    for i, c in enumerate(self.weekday_checks):
                        c.setChecked(i in allowed)
            elif task["sched_kind"] == "once":
                self.once_dt.setDateTime(
                    QDateTime.fromString(task["sched_param"], "yyyy-MM-dd HH:mm"))
            self.enabled_check.setChecked(bool(task["enabled"]))
            self.catchup_check.setChecked(bool(task.get("catchup", 1)))
            self.catchup_window_spin.setValue(int(task.get("catchup_window", 0) or 0))

    def _browse(self):
        if self.type_combo.currentData() == "yuflow":
            filt = "YuFlow 工作流 (*.json);;所有文件 (*)"
        else:
            filt = "Python 脚本 (*.py);;所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(self, "选择目标文件", "", filt)
        if path:
            self.target_edit.setText(path)

    def accept(self):
        if self.sched_combo.currentData() == "daily":
            if not self.time_list.values():
                QMessageBox.warning(self, self.windowTitle(), "请至少添加一个时间")
                return
            if not any(c.isChecked() for c in self.weekday_checks):
                QMessageBox.warning(self, self.windowTitle(), "请至少勾选一个星期的某一天")
                return
        super().accept()

    def values(self) -> dict:
        kind = self.sched_combo.currentData()
        weekdays = ""
        if kind == "interval":
            param = str(self.interval_spin.value())
        elif kind == "daily":
            param = self.time_list.values()
            # 全勾 = 每天，存空串（与旧数据一致）
            if not all(c.isChecked() for c in self.weekday_checks):
                weekdays = ",".join(
                    str(i) for i, c in enumerate(self.weekday_checks) if c.isChecked())
        elif kind == "once":
            param = self.once_dt.dateTime().toString("yyyy-MM-dd HH:mm")
        else:  # manual 无参数
            param = ""
        return {
            "name": self.name_edit.text().strip() or "未命名任务",
            "type": self.type_combo.currentData(),
            "target": self.target_edit.text().strip(),
            "sched_kind": kind,
            "sched_param": param,
            "enabled": self.enabled_check.isChecked(),
            "catchup": self.catchup_check.isChecked(),
            "catchup_window": (self.catchup_window_spin.value()
                               if self.catchup_check.isChecked() else 0),
            "weekdays": weekdays,
        }


class TriggerPage(QWidget):
    def __init__(self, store: TaskStore, scheduler: Scheduler, parent=None):
        super().__init__(parent)
        self._store = store
        self._scheduler = scheduler

        # 按钮行
        buttons = QHBoxLayout()
        for text, slot, cls in [
            ("新建", self._new, None), ("编辑", self._edit, None),
            ("删除", self._delete, "danger"),
            ("启用/禁用", self._toggle_enabled, None),
            ("立即运行", self._run_now, "primary"), ("停止", self._stop, None),
        ]:
            b = QPushButton(text)
            if cls:
                b.setProperty("class", cls)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["名称", "类型", "规则", "状态", "最近运行", "启用"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self._reload_log)

        # 日志区
        self.log_files_combo = QComboBox()
        self.log_files_combo.currentIndexChanged.connect(self._refresh_log_tail)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setProperty("class", "terminal")
        log_row = QHBoxLayout()
        log_row.addWidget(QLabel("日志文件"))
        log_row.addWidget(self.log_files_combo, 1)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addLayout(log_row)
        log_layout.addWidget(self.log_view)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(log_panel)
        splitter.setSizes([260, 240])

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(splitter)

        # 运行中任务的日志每秒刷新
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._refresh_log_tail)
        self._log_timer.start(1000)

        scheduler.changed.connect(self.reload)
        self.reload()

    # ---- 表格 ----
    @staticmethod
    def _rule_text(t: dict) -> str:
        if t["sched_kind"] == "interval":
            return f"每 {t['sched_param']} 分钟"
        if t["sched_kind"] == "daily":
            wd = (t.get("weekdays") or "").strip()
            if wd:
                days = "、".join(f"周{_WEEKDAY_NAMES[int(x)]}" for x in wd.split(","))
            else:
                days = "每天"
            times = " / ".join(t["sched_param"].split(","))
            return f"{days} {times}"
        if t["sched_kind"] == "once":
            return f"一次 {t['sched_param']}"
        return "手动"

    def reload(self):
        tasks = self._store.all()
        self.table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
            rule = self._rule_text(t)
            if t["sched_kind"] in ("once", "daily"):
                window_min = int(t.get("catchup_window", 0) or 0)
                if not t.get("catchup", 1):
                    rule += "（不补跑）"
                elif window_min > 0:
                    rule += (f"（补跑≤{window_min // 60}小时）" if window_min % 60 == 0
                             else f"（补跑≤{window_min}分钟）")
            status = _STATUS_NAMES.get(t["last_status"], t["last_status"] or "-")
            if self._scheduler.is_running(t["id"]):
                status = "运行中"
            values = [t["name"], _TYPE_NAMES.get(t["type"], t["type"]), rule,
                      status, t["last_run"] or "-", "是" if t["enabled"] else "否"]
            for col, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if col == 3:  # 状态列：彩色圆点 + 文字
                    item.setText(f"● {status}")
                    item.setForeground(QColor({"成功": theme_mod.SUCCESS, "运行中": theme_mod.SUCCESS,
                        "失败": theme_mod.DANGER, "已停止": theme_mod.TEXT_DIM,
                        "已取消": theme_mod.TEXT_DIM, "-": theme_mod.TEXT_DIM}.get(status, theme_mod.TEXT_DIM)))
                item.setData(Qt.ItemDataRole.UserRole, t["id"])
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def _selected_id(self) -> int | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    # ---- 任务操作 ----
    def _new(self):
        dlg = TaskDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            self._store.add(v["name"], v["type"], v["target"],
                            v["sched_kind"], v["sched_param"], v["enabled"],
                            v["catchup"], v["weekdays"], v["catchup_window"])
            self.reload()

    def _edit(self):
        tid = self._selected_id()
        if tid is None:
            return
        dlg = TaskDialog(self, self._store.get(tid))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            self._store.update(tid, v["name"], v["type"], v["target"],
                               v["sched_kind"], v["sched_param"], v["enabled"],
                               v["catchup"], v["weekdays"], v["catchup_window"])
            self.reload()

    def _delete(self):
        tid = self._selected_id()
        if tid is not None:
            self._scheduler.stop_task(tid)
            self._store.delete(tid)
            self.reload()

    def _toggle_enabled(self):
        tid = self._selected_id()
        if tid is not None:
            t = self._store.get(tid)
            self._store.set_enabled(tid, not t["enabled"])
            self.reload()

    def _run_now(self):
        tid = self._selected_id()
        if tid is not None:
            self._scheduler.run_task(tid, manual=True)

    def _stop(self):
        tid = self._selected_id()
        if tid is not None:
            self._scheduler.stop_task(tid)

    # ---- 日志 ----
    def _reload_log(self):
        tid = self._selected_id()
        self.log_files_combo.blockSignals(True)
        self.log_files_combo.clear()
        # 主日志置顶：全天触发概要，详情再看各任务日志
        self.log_files_combo.addItem("主日志", str(MAIN_LOG))
        n_task_logs = 0
        if tid is not None:
            for p in Scheduler.log_files(self._store.get(tid)):
                self.log_files_combo.addItem(p.name, str(p))
                n_task_logs += 1
        # 选中任务时默认展示该任务最新日志，未选中则停在主日志
        if n_task_logs:
            self.log_files_combo.setCurrentIndex(1)
        self.log_files_combo.blockSignals(False)
        self._last_log = None  # 切换任务后强制刷新日志
        self._refresh_log_tail()

    def _refresh_log_tail(self):
        path = self.log_files_combo.currentData()
        if not path:
            self.log_view.setPlainText("（暂无日志）")
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()[-20000:]  # 只显示末尾 20KB
        except OSError as e:
            text = f"（读取失败: {e}）"
        # 内容没变就不动：否则每秒刷新会打断用户选中文本/复制
        if (path, text) == getattr(self, "_last_log", None):
            return
        self._last_log = (path, text)
        sb = self.log_view.verticalScrollBar()
        was_at_bottom = sb.value() >= sb.maximum() - 4
        pos = sb.value()
        self.log_view.setPlainText(text)
        # 只有本来就在底部才跟随到最新；用户上翻查看历史时保持原位
        sb.setValue(sb.maximum() if was_at_bottom else min(pos, sb.maximum()))
