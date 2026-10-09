"""PRTS 主窗口：左侧导航按钮 + 页面堆栈。

看板页：选 spine 模型，再给各事件绑定动作（每个事件可绑多个，随机挑播）。
预览独立于配置：顶部「预览」下拉仅用于试看动作，改动事件配置不触发预览。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from core.assets import RelayLibrary
from core.calendar_store import CalendarStore
from core.pet_config import PetConfig
from core.pet_player import PetPlayer
from core.scheduler import Scheduler, TaskStore
from ui.calendar_page import CalendarPage
from ui.theme import ACCENT, BG_SIDEBAR
from ui.trigger_page import TriggerPage

# 事件键 → 界面标签（键与 pet_config 的字段一致）
_EVENTS = [("default", "默认"), ("start", "启动"), ("move", "移动"),
           ("interact", "双击互动"), ("close", "关闭"), ("idle", "待机")]


class NoWheelComboBox(QComboBox):
    """下拉框不吃滚轮：事件交给外层滚动区，避免滚动页面时误改选项。

    minimumContentsLength 让最小宽度跟内容解耦（动画名很长也不会把
    表单撑出横向溢出），空间不足时自动省略显示。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumContentsLength(8)
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def wheelEvent(self, e):
        e.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """同上：数字框也不吃滚轮。"""

    def wheelEvent(self, e):
        e.ignore()


class AnimListEditor(QWidget):
    """一个事件的动作列表编辑器：每行一个下拉框 + ▶预览 + ×，底部 ＋ 添加。

    值为动画名列表（保序、可重复；触发时随机挑一个，见 PetWindow）。
    选择变化只发 changed（不进预览）；▶ 单独发 previewRequested。
    """

    changed = Signal()
    previewRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anims: list[str] = []
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)
        add = QPushButton("＋")
        add.setFixedWidth(28)
        add.setProperty("class", "icon")
        add.setToolTip("添加一个动作")
        add.clicked.connect(lambda: self._add_row("", notify=True))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._rows)
        layout.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)

    def set_anims(self, anims: list[str]):
        """换模型后更新所有行的可选动作，保留已选（失效的置空）。"""
        self._anims = list(anims)
        for i in range(self._rows.count()):
            row = self._rows.itemAt(i).widget()
            combo = row.findChild(QComboBox)
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("（无）", "")
            for a in anims:
                combo.addItem(a, a)
            combo.setCurrentIndex(max(0, combo.findData(cur)))
            combo.blockSignals(False)

    def set_values(self, values: list[str]):
        """整体设置行。必须在 set_anims 之后调用，否则值选不中会被清空。"""
        while self._rows.count():
            self._take_row(0)
        for v in values:
            self._add_row(v, notify=False)

    def values(self) -> list[str]:
        out = []
        for i in range(self._rows.count()):
            row = self._rows.itemAt(i).widget()
            v = row.findChild(QComboBox).currentData()
            if v:
                out.append(v)
        return out

    def _take_row(self, i: int):
        item = self._rows.takeAt(i)
        item.widget().deleteLater()

    def _add_row(self, value: str, notify: bool):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        combo = NoWheelComboBox()
        combo.addItem("（无）", "")
        for a in self._anims:
            combo.addItem(a, a)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        pv = QPushButton("▶")
        pv.setFixedWidth(24)
        pv.setProperty("class", "icon")
        pv.setToolTip("预览这个动作")
        pv.clicked.connect(lambda: self._preview_row(row))
        rm = QPushButton("×")
        rm.setFixedWidth(24)
        rm.setProperty("class", "icon")
        rm.setToolTip("移除这个动作")
        rm.clicked.connect(lambda: self._remove_row(row))
        h.addWidget(combo, 1)
        h.addWidget(pv)
        h.addWidget(rm)
        self._rows.addWidget(row)
        if notify:
            self.changed.emit()

    def _preview_row(self, row: QWidget):
        v = row.findChild(QComboBox).currentData()
        if v:
            self.previewRequested.emit(v)

    def _remove_row(self, row: QWidget):
        self._rows.removeWidget(row)
        row.deleteLater()
        self.changed.emit()


class PetPage(QWidget):
    """看板娘设置页：选 spine 模型 + 各事件动作列表 + 独立预览。

    任何配置改动立即保存并回调 on_change(cfg)，由外层应用到桌宠。
    """

    def __init__(self, library: RelayLibrary, config_store: PetConfig,
                 on_change, parent=None):
        super().__init__(parent)
        self._library = library
        self._store = config_store
        self._on_change = on_change  # 回调(cfg dict)
        self._loading = False        # 初始填充期间不触发保存/回调

        # 配置为空或模型失效 → 用第一个可用模型生成默认配置
        self._cfg = config_store.load()
        if not library.spine_info(self._cfg.get("model", "")):
            first = library.first_spine()
            if first:
                self._cfg = PetConfig.default_for(library, first)
                config_store.save(self._cfg)

        controls = QGroupBox("看板")
        form = QFormLayout(controls)

        # 命名搭配：下拉读取 + 保存/删除
        self.preset_combo = NoWheelComboBox()
        self.preset_combo.setPlaceholderText("（选择已保存的搭配）")
        preset_save = QPushButton("保存")
        preset_save.setToolTip("把当前整套配置存为命名搭配")
        preset_del = QPushButton("删除")
        preset_del.setProperty("class", "danger")
        preset_del.setToolTip("删除选中的搭配")
        preset_row_w = QWidget()
        preset_row = QHBoxLayout(preset_row_w)
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(preset_save)
        preset_row.addWidget(preset_del)
        form.addRow("搭配", preset_row_w)

        self.model_combo = NoWheelComboBox()
        form.addRow("角色皮肤", self.model_combo)

        # 预览专用：只播效果，不进配置
        self.preview_combo = NoWheelComboBox()
        form.addRow("预览", self.preview_combo)

        auto_btn = QPushButton("一键配置")
        auto_btn.setProperty("class", "primary")
        auto_btn.setToolTip(
            "按动作名自动分配：默认←Idle、启动←Start、移动←Move、\n"
            "互动←Attack、关闭←Die、待机←Loop（没有的留空）")
        auto_btn.clicked.connect(self._on_auto_config)
        form.addRow("", auto_btn)

        # 基础比例：窗口尺寸 = 360 × 基础比例 × 滚轮缩放
        self.scale_spin = NoWheelDoubleSpinBox(
            minimum=0.1, maximum=10.0, singleStep=0.1, value=1.0)
        self.scale_spin.valueChanged.connect(self._on_config_changed)
        form.addRow("基础比例", self.scale_spin)

        self.editors: dict[str, AnimListEditor] = {}
        for key, label in _EVENTS:
            ed = AnimListEditor()
            ed.changed.connect(self._on_config_changed)
            ed.previewRequested.connect(self._preview_anim)
            self.editors[key] = ed
            form.addRow(label, ed)

        self.preview = PetPlayer(background="#ffffff")
        self.preview.setMinimumSize(320, 320)
        self._preview_model: tuple | None = None  # 预览页已加载的模型

        # 配置栏可滚动（条目多了超出可视高度时整体滚动；
        # 下拉框已禁用滚轮，滚轮事件都会落到这里）
        # 横向滑条禁用：widgetResizable 让内容宽度跟随视口自适应；
        # 最小宽度保证表单列（下拉+▶+×）不被裁掉
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(440)
        scroll.setWidget(controls)

        layout = QHBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.preview, 2)

        for m in library.models():
            self.model_combo.addItem(m, m)
        self.model_combo.currentIndexChanged.connect(self._reload_anims)
        self.preview_combo.currentIndexChanged.connect(self._on_preview_pick)
        preset_save.clicked.connect(self._on_preset_save)
        preset_del.clicked.connect(self._on_preset_delete)
        self.preset_combo.activated.connect(self._on_preset_load)

        self._reload_presets()
        self._loading = True
        idx = self.model_combo.findData(self._cfg.get("model", ""))
        self.model_combo.setCurrentIndex(max(0, idx))
        self._reload_anims()
        self.scale_spin.setValue(float(self._cfg.get("base_scale", 1.0)))
        self._loading = False
        self._reload_preview()

    # ---- 下拉填充 ----
    def _reload_anims(self):
        anims = (self._library.spine_info(
            self.model_combo.currentData()) or {}).get("animations", [])
        self.preview_combo.blockSignals(True)
        self.preview_combo.clear()
        for a in anims:
            self.preview_combo.addItem(a, a)
        self.preview_combo.blockSignals(False)
        for key, _ in _EVENTS:
            ed = self.editors[key]
            ed.set_anims(anims)  # 换模型后保留已选，失效的置空
            if self._loading:
                # 必须先 set_anims 再 set_values，否则选项不存在值会被清空
                ed.set_values(self._cfg.get(key, []))
        if not self._loading:
            self._collect()             # 模型失效的动作会被清掉，落库
            self._reload_preview()

    # ---- 配置收集 / 预览 ----
    def _collect_cfg(self):
        """界面 → self._cfg（不保存、不回调）。"""
        self._cfg["model"] = self.model_combo.currentData()
        for key, _ in _EVENTS:
            self._cfg[key] = self.editors[key].values()
        self._cfg["base_scale"] = self.scale_spin.value()

    def _collect(self):
        """界面 → cfg → 落库并通知外层应用到桌宠。"""
        self._collect_cfg()
        self._store.save(self._cfg)
        self._on_change(dict(self._cfg))

    def _reload_preview(self):
        """换模型时整套加载预览；默认池第一个动作作为初始预览动作。"""
        model = self._cfg.get("model", "")
        info = self._library.spine_info(model)
        if not info:
            return
        self._preview_model = model
        default = (self._cfg.get("default") or self._cfg.get("idle")
                   or info["animations"][:1])
        # 预览缩小到 25%：取景框放大，看全动作余量
        self.preview.play_spine(info["skel"], info["atlas"],
                                default[0] if default else "",
                                view_scale=0.25)

    def _on_preview_pick(self):
        if not self._loading:
            self._preview_anim(self.preview_combo.currentData())

    def _preview_anim(self, name: str):
        if name:
            self.preview.set_animation(name)

    def _on_config_changed(self):
        # 事件配置改动只落库 + 应用，不触发预览（预览走顶部「预览」下拉）
        if not self._loading:
            self._collect()

    def _on_auto_config(self):
        """一键配置：按关键词规则自动分配当前模型的全部动作。"""
        cfg = PetConfig.auto_for(self._library, self.model_combo.currentData())
        for key, _ in _EVENTS:
            self.editors[key].set_values(cfg[key])
        self._collect()

    # ---- 命名搭配 ----
    def _reload_presets(self, select: str = ""):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name in self._store.presets():
            self.preset_combo.addItem(name)
        if select:
            self.preset_combo.setCurrentText(select)
        else:
            self.preset_combo.setCurrentIndex(-1)  # 不默认选中，避免误读
        self.preset_combo.blockSignals(False)

    def _on_preset_save(self):
        name, ok = QInputDialog.getText(
            self, "保存搭配", "搭配名称：", text=self.preset_combo.currentText())
        name = name.strip()
        if not ok or not name:
            return
        if name in self._store.presets() and QMessageBox.question(
                self, "覆盖搭配", f"「{name}」已存在，覆盖？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._collect_cfg()  # 确保拿到界面上最新的值
        self._store.save_preset(name, dict(self._cfg))
        self._reload_presets(select=name)

    def _on_preset_load(self):
        self.load_preset_by_name(self.preset_combo.currentText())

    def load_preset_by_name(self, name: str):
        """按名加载搭配（看板娘右键「切换配置」也走这里，保证界面同步）。"""
        cfg = self._store.load_preset(name)
        if not cfg:
            return
        self._cfg = cfg
        self._loading = True
        idx = self.model_combo.findData(cfg.get("model", ""))
        self.model_combo.setCurrentIndex(max(0, idx))
        self._reload_anims()  # 会连带按 cfg 重填动作选项/编辑器
        self.scale_spin.setValue(float(cfg.get("base_scale", 1.0)))
        self._loading = False
        self._reload_preview()
        self._collect()       # 应用到桌宠并作为当前配置落库
        self._reload_presets(select=name)  # 下拉指到当前搭配

    def _on_preset_delete(self):
        name = self.preset_combo.currentText()
        if name:
            self._store.delete_preset(name)
            self._reload_presets()


class MainWindow(QMainWindow):
    def __init__(self, library: RelayLibrary, store: CalendarStore,
                 task_store: TaskStore,
                 scheduler: Scheduler, pet_config: PetConfig, on_pet_change,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("PRTS 桌面管理助手")
        self.resize(1040, 640)

        sidebar = QWidget()
        sidebar.setFixedWidth(150)
        sidebar.setStyleSheet(f"background-color: {BG_SIDEBAR};")
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(10, 14, 10, 14)
        nav.setSpacing(4)

        logo = QLabel(
            f"<span style='font-size: 34px;'>◈</span>"
            f"<span style='font-size: 19px; font-weight: bold;'> PRTS</span>"
        )
        logo.setStyleSheet(f"color: {ACCENT}; padding: 0 8px 14px 8px;")
        nav.addWidget(logo)

        self.stack = QStackedWidget()
        self.pet_page = PetPage(library, pet_config, on_pet_change)
        pages = [
            ("日历", CalendarPage(store)),
            ("触发器", TriggerPage(task_store, scheduler)),
            ("看板", self.pet_page),
        ]
        self._nav_buttons: list[QToolButton] = []
        for i, (title, page) in enumerate(pages):
            btn = QToolButton(text=title, checkable=True, autoExclusive=True)
            btn.setProperty("class", "nav")
            btn.setMinimumHeight(42)
            btn.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            nav.addWidget(btn)
            self._nav_buttons.append(btn)
            self.stack.addWidget(page)
        nav.addStretch(1)
        self._nav_buttons[0].setChecked(True)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)
