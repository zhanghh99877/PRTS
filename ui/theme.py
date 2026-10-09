"""PRTS 主题：明亮蓝白风。

集中存放全局 QSS 与调色板，main.py 通过 apply_theme(app) 一处生效。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# ---- 调色板 ----
BG_BASE = "#eef2f7"      # 主背景（淡蓝灰）
BG_PANEL = "#ffffff"     # 面板 / 输入框底
BG_CARD = "#e2eaf3"      # 卡片 / 悬停
BG_SIDEBAR = "#e6edf5"   # 侧边栏
BORDER = "#cfd9e4"       # 细边框
TEXT = "#2b3440"         # 主文字
TEXT_DIM = "#7c8896"     # 次要文字
ACCENT = "#2f8cff"       # 主蓝：选中 / 高亮 / 主按钮
ACCENT_HOVER = "#57a5ff"
ACCENT_PRESS = "#1f77e0"
DANGER = "#e5534b"       # 失败 / 过期 DDL
WARN = "#e69329"         # 临近 DDL
SUCCESS = "#2ea44f"      # 运行中 / 已完成

FONT_FAMILY = "Microsoft YaHei UI, Segoe UI, sans-serif"
MONO_FAMILY = "Cascadia Mono, JetBrains Mono, Consolas, monospace"

APP_QSS = f"""
/* ========== 基础 ========== */
QWidget {{
    background-color: {BG_BASE};
    color: {TEXT};
    font-family: {FONT_FAMILY};
    font-size: 13px;
}}
QToolTip {{
    background-color: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}
/* 悬浮看板窗靠 WA_TranslucentBackground 穿透，不能被全局 QWidget 底色
   糊成不透明方块。预览页的白底由页面 backgroundColor 提供，不受影响。 */
QWebEngineView {{ background-color: transparent; }}

/* ========== 侧边栏导航 ========== */
QToolButton[class="nav"] {{
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 6px;
    color: {TEXT_DIM};
    font-size: 14px;
    font-weight: bold;
    padding: 10px 16px;
    text-align: left;
}}
QToolButton[class="nav"]:hover {{
    background-color: #d8e4f2;
    color: {TEXT};
}}
QToolButton[class="nav"]:checked {{
    background-color: #d8e4f2;
    border-left: 3px solid {ACCENT};
    color: {ACCENT};
}}

/* ========== 日历页模式开关（计事 / 记账） ========== */
QToolButton[class="mode"] {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_DIM};
    font-weight: bold;
    padding: 5px 16px;
}}
QToolButton[class="mode"]:hover {{ border-color: {ACCENT}; color: {TEXT}; }}
QToolButton[class="mode"]:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
}}

/* ========== 按钮 ========== */
QPushButton {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 16px;
    min-height: 20px;
}}
QPushButton:hover {{ background-color: #eaf2fc; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: #d8e7fa; }}
QPushButton:disabled {{ color: {TEXT_DIM}; background-color: {BG_BASE}; }}
QPushButton[class="primary"] {{
    background-color: {ACCENT};
    border: none;
    color: #ffffff;
    font-weight: bold;
}}
QPushButton[class="primary"]:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton[class="primary"]:pressed {{ background-color: {ACCENT_PRESS}; }}
QPushButton[class="danger"]:hover {{
    background-color: {DANGER};
    border-color: {DANGER};
    color: #ffffff;
}}
/* 图标小按钮（▶/×/＋ 等定宽 24~28px）：去掉内边距，否则文字被挤没 */
QPushButton[class="icon"] {{
    padding: 0;
}}

/* ========== 输入控件 ========== */
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit, QPlainTextEdit {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: rgba(47, 140, 255, 90);
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus,
QDateTimeEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QTimeEdit:disabled {{ color: {TEXT_DIM}; background-color: {BG_BASE}; }}

QComboBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: rgba(47, 140, 255, 50);
    selection-color: {TEXT};
    outline: none;
}}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {BG_PANEL};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}

/* ========== 列表 / 表格 / 树 ========== */
QListWidget, QTableWidget, QTreeWidget {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: none;
    gridline-color: transparent;
    selection-background-color: transparent;
    selection-color: {TEXT};
}}
QListWidget::item, QTreeWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
    margin: 1px 4px;
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: rgba(47, 140, 255, 18);
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: rgba(47, 140, 255, 40);
}}
QTableWidget::item {{ padding: 4px 8px; border: none; }}
QTableWidget::item:selected {{ background-color: rgba(47, 140, 255, 40); }}
QHeaderView::section {{
    background-color: {BG_BASE};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: bold;
}}

/* ========== 标签页 ========== */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    top: -1px;
    background-color: {BG_PANEL};
}}
QTabBar::tab {{
    background-color: transparent;
    color: {TEXT_DIM};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 16px;
    font-weight: bold;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

/* ========== 菜单 ========== */
QMenu {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 24px 6px 20px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background-color: rgba(47, 140, 255, 45); }}
QMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 8px;
}}

/* ========== 滚动条 ========== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: #c3cfdd;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #a8b8ca; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: #c3cfdd;
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: #a8b8ca; }}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; height: 0; width: 0;
}}

/* ========== 分组框 / 分隔条 ========== */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 8px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {TEXT_DIM};
}}
QSplitter::handle {{ background-color: {BORDER}; }}
QSplitter::handle:vertical {{ height: 2px; }}
QSplitter::handle:horizontal {{ width: 2px; }}

/* ========== 日历 ========== */
QCalendarWidget QWidget {{ alternate-background-color: {BG_PANEL}; }}
QCalendarWidget QAbstractItemView {{
    background-color: {BG_PANEL};
    selection-background-color: transparent;
    selection-color: {TEXT};
    outline: none;
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {BG_BASE};
    border-bottom: 1px solid {BORDER};
}}
QCalendarWidget QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 4px;
    color: {TEXT};
    font-weight: bold;
    padding: 4px 8px;
}}
QCalendarWidget QToolButton:hover {{ background-color: {BG_CARD}; }}
QCalendarWidget QMenu {{ background-color: {BG_PANEL}; }}
QCalendarWidget QSpinBox {{
    min-width: 60px;
}}

/* ========== 日志终端面板 ========== */
QPlainTextEdit[class="terminal"] {{
    background-color: #f4f7fb;
    color: #4a5563;
    font-family: {MONO_FAMILY};
    font-size: 12px;
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
"""


def apply_theme(app: QApplication):
    """Fusion 风格 + 蓝白调色板 + 全局 QSS，一处调用。"""
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(BG_BASE))
    p.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Base, QColor(BG_PANEL))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    p.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Button, QColor(BG_PANEL))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DIM))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_PANEL))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    app.setPalette(p)
    app.setStyleSheet(APP_QSS)
