"""PRTS 桌面管理助手 —— 入口。

外部看板娘（PetWindow）+ 主页面（MainWindow）+ 系统托盘。
"""

import os
import sys
import time
from datetime import datetime

# 必须在 QtWebEngine 初始化前设置：允许视频无手势自动播放
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS", "--autoplay-policy=no-user-gesture-required --allow-file-access-from-files"
)

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtCore import Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.assets import RelayLibrary, title_path
from core.calendar_store import CalendarStore
from core.pet_config import PetConfig
from core.scheduler import Scheduler, TaskStore
from ui.main_window import MainWindow
from ui.pet_window import PetWindow
from ui.theme import apply_theme
from ui.toast import show_trigger_toast

REMIND_INTERVAL_MS = 30_000  # DDL 提醒扫描间隔


INSTANCE_KEY = "prts-desktop-manager"


def ensure_single_instance() -> QLocalServer:
    """单实例：已有实例在跑 → 通知它退出，本实例接管（新实例永远赢）。

    返回本实例的本地 server（必须一直持有：下一个实例靠它通知我们退）。
    """
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        # 老实例在跑：通知它退出，等它让出管道名
        sock.write(b"takeover")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        for _ in range(30):  # 最多等 3 秒
            server = QLocalServer()
            if server.listen(INSTANCE_KEY):
                return server
            time.sleep(0.1)
    QLocalServer.removeServer(INSTANCE_KEY)  # 上次崩溃残留的空壳
    server = QLocalServer()
    server.listen(INSTANCE_KEY)
    return server


def make_icon() -> QIcon:
    """托盘/窗口图标：Title/title.png，没有就画个圆占位。"""
    avatar = title_path()
    if avatar:
        return QIcon(str(avatar))
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setBrush(QColor("#3a7bd5"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 28, 28)
    p.end()
    return QIcon(pm)


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关主窗口不退出，看板娘/托盘仍在

    # 单实例：已有实例在跑时，新实例通知它退出后接管
    instance_server = ensure_single_instance()

    def _on_takeover():
        conn = instance_server.nextPendingConnection()
        if conn:
            conn.disconnectFromServer()
        QApplication.quit()  # 立刻退，不走关闭动画（新实例要端口）

    instance_server.newConnection.connect(_on_takeover)

    apply_theme(app)

    library = RelayLibrary()
    store = CalendarStore()
    task_store = TaskStore(store)
    scheduler = Scheduler(task_store)
    pet_cfg = PetConfig()

    pet = PetWindow(library, pet_cfg)
    main_win = MainWindow(
        library, store, task_store, scheduler, pet_cfg,
        on_pet_change=lambda cfg: (pet_cfg.save(cfg), pet.apply_config(cfg)),
    )
    pet.main_window = main_win  # 右键菜单“隐藏主页面”用

    # 应用看板配置（PetPage 构造时已把无效配置替换为默认并落库）
    pet.apply_config(pet_cfg.load())

    pet.openRequested.connect(lambda: (main_win.show(), main_win.raise_(), main_win.activateWindow()))

    tray = QSystemTrayIcon(make_icon(), parent=app)
    main_win.setWindowIcon(make_icon())
    tray_menu = QMenu()
    tray_menu.addAction("打开主页面", lambda: (main_win.show(), main_win.raise_()))
    tray_menu.addAction("退出", pet.request_quit)
    tray.setContextMenu(tray_menu)
    tray.setToolTip("PRTS 桌面管理助手")
    tray.show()

    # DDL 提醒：定时扫描，托盘弹通知，每条只提醒一次
    def check_reminders():
        for e in store.due_reminders():
            overdue = e["ddl_dt"] < datetime.now()
            status = "已过期！" if overdue else "即将截止"
            tray.showMessage(
                f"DDL {status}",
                f"{e['title']}\n截止：{e['ddl_dt'].strftime('%m-%d %H:%M')}",
                QSystemTrayIcon.MessageIcon.Warning,
                10_000,
            )
            store.mark_notified(e["id"])

    remind_timer = QTimer(app)
    remind_timer.timeout.connect(check_reminders)
    remind_timer.start(REMIND_INTERVAL_MS)
    QTimer.singleShot(5_000, check_reminders)  # 启动后先查一轮

    # 自动触发确认：右下角弹窗倒计时 5 秒，不取消才启动脚本
    scheduler.trigger_pending.connect(
        lambda tid, name: show_trigger_toast(
            tid, name, scheduler.confirm_trigger, scheduler.cancel_trigger))

    pet.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
