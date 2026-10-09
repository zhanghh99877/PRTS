"""日历页：月历 + 右侧当日事项面板。

事项圆点 / DDL 变色 + 当日事项编辑面板。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, QEvent, QRect, Qt, QTime
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from core.calendar_store import CalendarStore
from ui.theme import ACCENT, DANGER, TEXT_DIM, WARN

_COLOR_DDL_SOON = QColor(DANGER)  # 3 天内 / 已过期 DDL：红字
_COLOR_DDL_FAR = QColor(WARN)     # 更远的 DDL：橙点
_COLOR_EVENT = QColor(ACCENT)     # 普通事项：蓝点
_ACCENT = QColor(ACCENT)
_TEXT_DIM = QColor(TEXT_DIM)


class MarkedCalendar(QCalendarWidget):
    """按事项/DDL 绘制格子标记。"""

    def __init__(self, store: CalendarStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._marks: dict[str, dict] = {}
        # 不显示左侧周数
        self.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.refresh_marks()

    def refresh_marks(self):
        self._marks = self._store.month_marks(
            self.yearShown(), self.monthShown())
        self.updateCells()

    def _goto(self, year, month):
        super().setCurrentPage(year, month)
        self.refresh_marks()

    def showNextMonth(self):
        self._goto(*_shift(self.yearShown(), self.monthShown(), 1))

    def showPreviousMonth(self):
        self._goto(*_shift(self.yearShown(), self.monthShown(), -1))

    def paintCell(self, painter: QPainter, rect: QRect, date: QDate):
        super().paintCell(painter, rect, date)
        # 今天：主色实线圆角框；选中日期：主色虚线框（错开内缩，可叠加）
        if date == QDate.currentDate():
            painter.save()
            painter.setPen(QPen(_ACCENT, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
            painter.restore()
        if date == self.selectedDate():
            painter.save()
            painter.setPen(QPen(_ACCENT, 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 5, 5)
            painter.restore()
        self._paint_event_marks(painter, rect, date)

    def _paint_event_marks(self, painter: QPainter, rect: QRect, date: QDate):
        key = date.toString("yyyy-MM-dd")
        m = self._marks.get(key)
        if not m:
            return
        today = datetime.now().date()
        d = date.toPython()
        # DDL：临近/过期红字重画
        if m["has_pending_ddl"] and (m["overdue_ddl"] or (d - today).days <= 3):
            painter.save()
            painter.setPen(_COLOR_DDL_SOON)
            f = QFont(painter.font())
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))
            painter.restore()
        # 圆点：DDL 橙、事项蓝，画在格子底部
        dots = []
        if m["has_pending_ddl"]:
            dots.append(_COLOR_DDL_FAR)
        if m["has_event"]:
            dots.append(_COLOR_EVENT)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        r = 3
        total_w = len(dots) * 2 * r + (len(dots) - 1) * 4
        x = rect.center().x() - total_w // 2 + r
        y = rect.bottom() - 6
        for color in dots:
            painter.setBrush(QBrush(color))
            painter.drawEllipse(x - r, y - r, 2 * r, 2 * r)
            x += 2 * r + 4
        painter.restore()


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    if month > 12:
        return year + 1, 1
    if month < 1:
        return year - 1, 12
    return year, month


class CalendarPage(QWidget):
    def __init__(self, store: CalendarStore, parent=None):
        super().__init__(parent)
        self._store = store

        self.calendar = MarkedCalendar(store)
        today_btn = QPushButton("回到今天")
        today_btn.clicked.connect(self._goto_today)

        # 右侧面板：当日 / 一览 两个标签页
        self.date_label = QLabel()
        self.date_label.setStyleSheet("font-weight: bold;")
        self.events = QListWidget()
        self.events.itemDoubleClicked.connect(self._toggle_done)
        self.events.itemClicked.connect(self._load_into_form)
        self.events.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.events.customContextMenuRequested.connect(
            lambda pos: self._show_item_menu(self.events, pos)
        )

        self.overview = QListWidget()
        self.overview.itemClicked.connect(self._load_into_form)
        self.overview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.overview.customContextMenuRequested.connect(
            lambda pos: self._show_item_menu(self.overview, pos)
        )

        day_tab = QWidget()
        day_layout = QVBoxLayout(day_tab)
        day_layout.setContentsMargins(0, 4, 0, 0)
        day_layout.addWidget(self.date_label)
        day_layout.addWidget(self.events, 1)

        overview_tab = QWidget()
        overview_layout = QVBoxLayout(overview_tab)
        overview_layout.setContentsMargins(0, 4, 0, 0)
        overview_layout.addWidget(self.overview, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(day_tab, "当日")
        self.tabs.addTab(overview_tab, "一览")

        # 添加表单：标题独占一行，详细内容第二行，选项第三行
        # 点击列表中的事项会回填到这里，「添加」变「修改」
        self._editing: dict | None = None  # 正在编辑的事件（None = 添加模式）
        self.title_edit = QLineEdit(placeholderText="要做什么？")
        self.title_edit.setMinimumHeight(32)
        self.title_edit.returnPressed.connect(self._submit)
        self.note_edit = QLineEdit(placeholderText="详细内容…（可留空）")
        self.note_edit.setMinimumHeight(28)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("事项", "event")
        self.kind_combo.addItem("DDL", "ddl")
        self.time_check = QCheckBox("指定时间")
        self.time_edit = QTimeEdit(datetime.now().time())
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setEnabled(False)
        self.time_check.toggled.connect(self.time_edit.setEnabled)
        self.kind_combo.currentIndexChanged.connect(
            lambda i: self.time_check.setChecked(self.kind_combo.currentData() == "ddl")
        )
        self.submit_btn = QPushButton("添加")
        self.submit_btn.setProperty("class", "primary")
        self.submit_btn.clicked.connect(self._submit)

        form_row2 = QHBoxLayout()
        form_row2.addWidget(self.kind_combo)
        form_row2.addWidget(self.time_check)
        form_row2.addWidget(self.time_edit)
        form_row2.addStretch(1)
        form_row2.addWidget(self.submit_btn)

        ops = QHBoxLayout()
        done_btn = QPushButton("完成 / 取消完成")
        # 日期直接改：修改模式 = 事项日期（改动即移动事项）；
        # 添加模式 = 新事项的落点（默认跟日历选中那天）
        self.date_edit = QDateEdit()
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setCalendarPopup(True)  # 也可点箭头弹月历
        self.date_edit.dateChanged.connect(self._on_date_edit_changed)
        del_btn = QPushButton("删除")
        del_btn.setProperty("class", "danger")
        done_btn.clicked.connect(self._toggle_done)
        del_btn.clicked.connect(self._delete)
        ops.addWidget(done_btn)
        ops.addWidget(self.date_edit)
        ops.addWidget(del_btn)
        ops.addStretch(1)

        event_panel = QWidget()
        right = QVBoxLayout(event_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.tabs, 1)
        right.addLayout(ops)
        right.addWidget(self.title_edit)
        right.addWidget(self.note_edit)
        right.addLayout(form_row2)

        left = QVBoxLayout()
        left.addWidget(self.calendar, 1)
        left.addWidget(today_btn)

        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(event_panel, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)

        self.calendar.selectionChanged.connect(self._on_date_changed)
        self.calendar.currentPageChanged.connect(self._on_page_changed)
        # 点列表空白处：退出编辑模式回到「添加」，方便直接新建
        for lst in (self.events, self.overview):
            lst.viewport().installEventFilter(self)
        self._refresh_all()
        self._sync_move_ui()

    def _sync_move_ui(self):
        """日期框显示值：修改模式 = 事项日期；添加模式 = 日历选中那天。"""
        self.date_edit.blockSignals(True)
        try:
            if self._editing is not None:
                self.date_edit.setDate(
                    QDate.fromString(self._editing["date"], "yyyy-MM-dd"))
            else:
                self.date_edit.setDate(self.calendar.selectedDate())
        finally:
            self.date_edit.blockSignals(False)

    def eventFilter(self, obj, event):
        """列表 viewport 的空白点击 → 退出编辑模式。"""
        if event.type() == QEvent.Type.MouseButtonPress:
            lst = obj.parent()
            if isinstance(lst, QListWidget) and lst.itemAt(event.position().toPoint()) is None:
                self._reset_form()
        return super().eventFilter(obj, event)

    # ---- 数据刷新 ----
    def _reload(self):
        d = self.calendar.selectedDate()
        self.date_label.setText(d.toString("yyyy年MM月dd日"))
        self.events.clear()
        for e in self._store.events_on(d.toString("yyyy-MM-dd")):
            tag = "DDL" if e["kind"] == "ddl" else "事项"
            time = f" {e['time']}" if e["time"] else ""
            item = QListWidgetItem(f"[{tag}{time}] {e['title']}")
            item.setData(Qt.ItemDataRole.UserRole, e)
            if e["done"]:
                f = item.font()
                f.setStrikeOut(True)
                item.setFont(f)
                item.setForeground(_TEXT_DIM)
            self.events.addItem(item)

    def _refresh_all(self):
        self._reload()
        self._reload_overview()
        self.calendar.refresh_marks()
        # 正在编辑的事件被删除/移出列表（如标记完成后从一览消失）→ 回添加模式
        if self._editing is not None:
            alive = any(
                (lst.item(i).data(Qt.ItemDataRole.UserRole) or {}).get("id")
                == self._editing["id"]
                for lst in (self.events, self.overview)
                for i in range(lst.count())
            )
            if not alive:
                self._reset_form()

    def _on_date_changed(self):
        self._reset_form()  # 换日期即退出编辑；修改自动保存（不移动事项）
        self.tabs.setCurrentIndex(0)  # 右栏切回「当日」
        self._reload()
        self._sync_move_ui()  # 添加模式下日期显示跟日历选中走

    def _on_page_changed(self, year: int, month: int):
        # 导航栏翻月/菜单跳月都走这里，顺带刷新格子标记
        self.calendar.refresh_marks()

    def _goto_today(self):
        today = QDate.currentDate()
        self.calendar.setCurrentPage(today.year(), today.month())
        self.calendar.setSelectedDate(today)

    # ---- 一览 ----
    def _reload_overview(self):
        self.overview.clear()
        today = datetime.now().date()
        for e in self._store.pending_overview():
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            days = (d - today).days
            if days < 0:
                countdown = f"已过期{-days}天"
            elif days == 0:
                countdown = "今天"
            elif days == 1:
                countdown = "明天"
            else:
                countdown = f"还剩{days}天"
            tag = "DDL" if e["kind"] == "ddl" else "事项"
            time = f" {e['time']}" if e["time"] else ""
            item = QListWidgetItem(
                f"[{tag}] {d.strftime('%Y-%m-%d')}{time} {e['title']}（{countdown}）"
            )
            item.setData(Qt.ItemDataRole.UserRole, e)
            if e["kind"] == "ddl":
                item.setForeground(_COLOR_DDL_SOON if days <= 3 else _COLOR_DDL_FAR)
            self.overview.addItem(item)

    # ---- 操作 ----
    def _load_into_form(self, item: QListWidgetItem):
        """单击列表项：先保存对上一项的修改，再把这项回填到表单。

        注意：item.data() 返回的是快照副本，列表刷新后 item 即失效，
        所以必须先取 id、再刷新、再从数据库取最新数据回填。
        """
        eid = item.data(Qt.ItemDataRole.UserRole)["id"]
        self._save_editing()
        self._refresh_all()  # 列表显示与缓存对齐数据库
        e = self._store.get(eid)
        if e is None:
            return
        self._editing = e
        self.title_edit.setText(e["title"])
        self.note_edit.setText(e.get("note") or "")
        # 先设类型再设时间勾选：类型联动会自动勾/取消“指定时间”，需要覆盖
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(e["kind"]))
        self.time_check.setChecked(bool(e["time"]))
        if e["time"]:
            self.time_edit.setTime(QTime.fromString(e["time"], "HH:mm"))
        self.submit_btn.setText("修改")
        self._sync_move_ui()

    def _save_editing(self):
        """把表单当前值写回正在编辑的事项（标题为空则放弃保存）。"""
        e = self._editing
        if e is None:
            return
        title = self.title_edit.text().strip()
        if not title:
            return
        time = (self.time_edit.time().toString("HH:mm")
                if self.time_check.isChecked() else None)
        self._store.update_event(
            e["id"], title, self.kind_combo.currentData(), time,
            self.note_edit.text().strip())

    def _reset_form(self):
        """退出编辑回到添加模式；编辑中的修改自动保存并刷新列表显示。"""
        if self._editing is not None:
            self._save_editing()
            self.title_edit.clear()
            self.note_edit.clear()
            self._editing = None  # 先清再刷新：_refresh_all 的存活检查靠它防递归
            self._refresh_all()
        self.submit_btn.setText("添加")
        self._sync_move_ui()

    def _submit(self):
        if self._editing is not None:
            # 修改：退出编辑即自动保存（_reset_form 内含刷新），与
            # 点空白/换日期行为一致
            self._reset_form()
            return
        title = self.title_edit.text().strip()
        if not title:
            return
        time = self.time_edit.time().toString("HH:mm") if self.time_check.isChecked() else None
        note = self.note_edit.text().strip()
        self._store.add_event(self.date_edit.date().toString("yyyy-MM-dd"),
                              title, self.kind_combo.currentData(), time, note)
        self.title_edit.clear()
        self.note_edit.clear()
        self._sync_move_ui()  # 日期框复位跟日历选中走
        self._refresh_all()

    def _current_list(self) -> QListWidget:
        return self.overview if self.tabs.currentIndex() == 1 else self.events

    def _selected_event(self) -> dict | None:
        item = self._current_list().currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _set_done(self, e: dict):
        self._store.set_done(e["id"], not e["done"])
        self._refresh_all()

    def _delete_event(self, e: dict):
        self._store.delete(e["id"])
        self._refresh_all()

    def _toggle_done(self):
        e = self._selected_event()
        if e:
            self._set_done(e)

    def _delete(self):
        e = self._selected_event()
        if e:
            self._delete_event(e)

    def _on_date_edit_changed(self, d: QDate):
        """日期框被直接改动。

        修改模式：事项移到所选日期并跳过去看（setSelectedDate 触发的
        _on_date_changed 会顺带保存表单并回到添加模式）；
        添加模式：日期框的值就是新事项的落点，无需其他动作。
        """
        if self._editing is None:
            return
        new_date = d.toString("yyyy-MM-dd")
        if new_date == self._editing["date"]:
            return
        self._store.move_event(self._editing["id"], new_date)
        self.calendar.setCurrentPage(d.year(), d.month())
        self.calendar.setSelectedDate(d)
        self._refresh_all()

    def _show_item_menu(self, lst: QListWidget, pos):
        """右键菜单：跳转 / 完成 / 删除（当日与一览两个列表共用）。"""
        item = lst.itemAt(pos)
        if not item:
            return
        lst.setCurrentItem(item)
        e = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act_jump = QAction("跳转到日期", self)
        act_done = QAction("取消完成" if e["done"] else "完成", self)
        act_del = QAction("删除", self)
        act_jump.triggered.connect(lambda: self._jump_to_date(e))
        act_done.triggered.connect(lambda: self._set_done(e))
        act_del.triggered.connect(lambda: self._delete_event(e))
        menu.addAction(act_jump)
        menu.addAction(act_done)
        menu.addAction(act_del)
        menu.exec(lst.viewport().mapToGlobal(pos))

    def _jump_to_date(self, e: dict):
        """日历跳到该事项所在日期，右栏回「当日」页。"""
        d = QDate.fromString(e["date"], "yyyy-MM-dd")
        self.calendar.setCurrentPage(d.year(), d.month())
        self.calendar.setSelectedDate(d)
        self.tabs.setCurrentIndex(0)
