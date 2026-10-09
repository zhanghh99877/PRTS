"""自动触发确认窗：右下角弹出，倒计时 5 秒，不取消才真正启动脚本。

主窗口常被隐藏（托盘应用），所以弹窗是独立的无边框置顶窗口，
定位在屏幕右下角；多个弹窗向上堆叠，关闭后其余下移补位。
倒计时结束 = 确认（启动任务）；点「取消」= 跳过本次触发。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ui.theme import BG_PANEL, BORDER, DANGER, TEXT_DIM

DURATION_S = 5      # 倒计时（秒），归零即启动任务
MARGIN = 16         # 距屏幕右/下边缘的距离
GAP = 8             # 弹窗之间的纵向间距

_toasts: list["TriggerToast"] = []  # 存活的弹窗，用于堆叠定位


class TriggerToast(QWidget):
    """「即将自动触发 ×××」确认窗：倒计时结束启动，点取消则跳过本次。"""

    def __init__(self, task_id: int, name: str, on_confirm, on_cancel, parent=None):
        super().__init__(parent)
        self._task_id = task_id
        self._on_confirm = on_confirm  # 回调(task_id)：启动任务
        self._on_cancel = on_cancel    # 回调(task_id)：跳过本次
        self._remain = DURATION_S

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(
            Qt.WindowType.Tool            # 不出现在任务栏
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(
            f"TriggerToast {{ background-color: {BG_PANEL};"
            f" border: 1px solid {BORDER}; border-radius: 8px; }}")

        icon = QLabel("⏰")
        text = QLabel(f"即将自动触发「{name}」")
        text.setStyleSheet("font-weight: bold;")
        self._hint = QLabel("")
        self._hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setToolTip("跳过这次运行")
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {BORDER}; border-radius: 6px;"
            f" padding: 4px 12px; }}"
            f"QPushButton:hover {{ background-color: {DANGER};"
            f" border-color: {DANGER}; color: #ffffff; }}")
        self._cancel_btn.clicked.connect(self._cancel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(icon)
        layout.addWidget(text)
        layout.addWidget(self._hint)
        layout.addWidget(self._cancel_btn)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1_000)
        self._update_hint()

    # ---- 倒计时 ----
    def _update_hint(self):
        self._hint.setText(f"{self._remain}s 后启动")

    def _tick(self):
        self._remain -= 1
        if self._remain <= 0:
            self._on_confirm(self._task_id)
            self.close()
        else:
            self._update_hint()

    def _cancel(self):
        self._on_cancel(self._task_id)
        self.close()

    # ---- 堆叠定位 ----
    def show(self):
        super().show()
        _toasts.append(self)
        _restack()

    def closeEvent(self, e):
        if self in _toasts:
            _toasts.remove(self)
            _restack()
        super().closeEvent(e)


def _restack():
    """按加入顺序从屏幕右下角向上堆叠（新的在最下）。"""
    screen = QGuiApplication.primaryScreen()
    if not screen:
        return
    area = screen.availableGeometry()
    y = area.bottom() - MARGIN
    for t in reversed(_toasts):
        t.adjustSize()
        y -= t.height()
        t.move(area.right() - MARGIN - t.width(), y)
        y -= GAP


def show_trigger_toast(task_id: int, name: str, on_confirm, on_cancel):
    """弹出一个自动触发确认窗。倒计时结束调 on_confirm，点取消调 on_cancel。"""
    toast = TriggerToast(task_id, name, on_confirm, on_cancel)
    toast.show()
