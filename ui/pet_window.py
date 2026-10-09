"""外部看板悬浮窗：无边框透明、可拖动、可滚轮缩放。

行为状态机（配置见 core/pet_config.py）：
  常态 → default 动作循环
  待机 → 常态下每 10s 从 idle 池随机抽一个播一次 → 回常态
  启动 → start 动作播一次 → 常态
  拖动 → move 动作循环，松手回常态
  双击 → interact 动作播一次 → 常态
  退出 → close 动作播完 → 真正退出
  单击 → 打开主页面（openRequested）

点击穿透：窗口常驻 WS_EX_TRANSPARENT（鼠标穿透到下层窗口），定时器轮询
光标位置，仅当光标落在角色不透明像素上时临时取消穿透使其可交互。
角色位图由页面 JS（window._petMask）从 WebGL 画面降采样生成。
按住左键期间保持可交互（拖动不被中断）。
"""

from __future__ import annotations

import ctypes
import os
import random
import subprocess
import sys
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

from core.pet_player import PetPlayer

_MASK_PAD = 0.03    # 位图判定的外扩余量（格为单位另加 1 格容差）
_GRID = 45          # 与 JS 侧采样网格一致
_HIT_SCALE = 0.5    # 可点击区域：角色包围盒向中心收缩后的比例（0.5 = 宽高各减半）

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x20
_user32 = ctypes.windll.user32

_BASE_SIZE = 360    # 基准窗口尺寸（1.0x）
_SCALE_MIN = 0.3    # 最小缩小到 30%
_SCALE_MAX = 2.0    # 最大扩大 1 倍（即 2.0x）
_ZOOM_STEP = 1.1    # 每格滚轮的缩放倍率

_IDLE_TICK_MS = 10_000  # 待机抽取间隔：常态下每 10s 从 idle 池抽一个播一次


class PetWindow(PetPlayer):
    openRequested = Signal()

    def __init__(self, library, config_store=None, parent=None):
        super().__init__(background="transparent", parent=parent)
        self._library = library
        self._cfg_store = config_store   # PetConfig，用于取景框缓存（可空=不缓存）
        self._vp_sig: tuple | None = None  # 当前取景框缓存键 (model, sig)
        self._cfg: dict = {}
        self._model: str | None = None      # 当前已加载模型名（文件夹名）
        self._state = "default"            # default | start | idle | move | interact | closing
        self._current_anim = ""

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.resize(_BASE_SIZE, _BASE_SIZE)
        self._scale = 1.0                    # 滚轮缩放（0.3 ~ 2.0），启动默认 1.0
        self._base_scale = 1.0               # 基础比例（配置），与滚轮缩放乘算
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 60, screen.bottom() - self.height() - 60)
        self._hit_rect: QRect | None = None  # 角色包围盒（窗口逻辑坐标，展示用）
        self._grid: str | None = None        # 45x45 不透明位图，逐像素判定用
        self._char_bbox: tuple | None = None  # 角色归一化包围盒 (l,t,r,b)，未加余量
        self._through = False

        # JS 桥：行为事件
        self.bridge.dragStarted.connect(self._on_drag_start)
        self.bridge.dragDelta.connect(self._on_drag)
        self.bridge.dragEnded.connect(self._on_drag_end)
        self.bridge.petClicked.connect(self.openRequested.emit)
        self.bridge.petDoubleClicked.connect(self._on_interact)
        self.bridge.wheelDelta.connect(self._on_wheel_zoom)
        self.bridge.animCompleted.connect(self._on_anim_complete)
        self.bridge.viewportComputed.connect(self._on_viewport_computed)

        # 轮询角色位图（页面 JS 从 WebGL 画面降采样生成）
        self._bbox_timer = QTimer(self)
        self._bbox_timer.timeout.connect(self._update_hit_rect)
        self._bbox_timer.start(300)

        # 穿透轮询：光标在角色上才可交互，否则鼠标穿透到下层
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_cursor)
        self._poll_timer.start(120)

        # 待机抽取：常态下每 10s 从 idle 池随机抽一个播一次
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._on_idle_tick)
        self._idle_timer.start(_IDLE_TICK_MS)

    # ---- 配置 / 行为 ----
    def apply_config(self, cfg: dict):
        """应用看板配置。模型变了整套重载（播 start），没变只更新动作绑定。"""
        self._cfg = cfg
        # 基础比例：与滚轮缩放乘算，随时可调
        base = float(cfg.get("base_scale", 1.0) or 1.0)
        if base != self._base_scale:
            self._base_scale = base
            self._apply_size()
        model = cfg.get("model", "")
        if model == self._model:
            return
        info = self._library.spine_info(model)
        if not info:
            return
        self._model = model
        anims = info["animations"]
        # 取景并集：全部已配置动作（去重保序），大动作也播得下。
        # 结果按 模型+动作集+文件mtime 缓存进库，启动直接套用不重算
        fit_all = list(dict.fromkeys(
            a for key in ("default", "start", "move", "interact", "close", "idle")
            for a in self._cfg.get(key, []) if a))
        default = self._default_anim()
        # 取景参照必须确定性（排序后取第一个），否则随机挑选导致缓存永不命中
        ref_pool = sorted(a for a in (self._cfg.get("default")
                                      or self._cfg.get("idle") or []) if a)
        fit_ref = ref_pool[0] if ref_pool else default
        import json as _json
        sig = _json.dumps({"anims": sorted(fit_all), "fit": fit_ref,
                           "mtime": info["skel"].stat().st_mtime})
        cached_vp = self._cfg_store.get_viewport(model, sig) \
            if self._cfg_store else None
        self._vp_sig = (model, sig)  # 算出来回传时按这个键存
        start = self._pick_from("start")
        if start and start in anims:
            self._state = "start"
            self._current_anim = start
            # 取景框按默认动作算，Start 再大也不压缩主模型
            self.play_spine(info["skel"], info["atlas"], start,
                            loop=False, fit=fit_ref, fit_all=fit_all,
                            viewport=cached_vp)
        else:
            self._state = "default"
            self._current_anim = default
            self.play_spine(info["skel"], info["atlas"], default, loop=True,
                            fit=fit_ref, fit_all=fit_all, viewport=cached_vp)

    def _pick_from(self, key: str) -> str:
        """从事件的动作列表随机挑一个（尽量避免和当前动作重复）。"""
        pool = [a for a in self._cfg.get(key, []) if a]
        if not pool:
            return ""
        others = [a for a in pool if a != self._current_anim]
        return random.choice(others or pool)

    def _default_anim(self) -> str:
        """常态动作：default 池优先；没配则退回 idle 池（兼容旧配置）。"""
        return self._pick_from("default") or self._pick_from("idle")

    def _go_default(self):
        self._state = "default"
        anim = self._default_anim()
        if anim:
            self._current_anim = anim
            self.set_animation(anim, loop=True)

    def _on_idle_tick(self):
        # 待机：只有常态下才触发；抽一个播一次，播完由完成事件送回常态
        if self._state != "default":
            return
        anim = self._pick_from("idle")
        if anim:
            self._state = "idle"
            self._current_anim = anim
            self.set_animation(anim, loop=False)

    def _on_drag_start(self):
        if self._state == "closing":
            return
        move = self._pick_from("move")
        self._state = "move"
        if move:
            self._current_anim = move
            self.set_animation(move, loop=True)

    def _on_drag_end(self):
        if self._state == "move":
            self._go_default()

    def _on_interact(self):
        if self._state not in ("default", "idle"):
            return
        interact = self._pick_from("interact")
        if interact:
            self._state = "interact"
            self._current_anim = interact
            self.set_animation(interact, loop=False)

    def _on_anim_complete(self, name: str):
        if name != self._current_anim:
            return
        # 一次性动作（启动/互动/待机）播完 → 回常态
        if self._state in ("start", "interact", "idle"):
            self._go_default()
        # 关闭动作播完 → 真正退出
        elif self._state == "closing":
            QApplication.quit()

    def _on_viewport_computed(self, vp_json: str):
        """JS 算完并集取景框 → 按当前缓存键落库，下次启动直接套用。"""
        if not self._cfg_store or not self._vp_sig:
            return
        import json as _json
        try:
            vp = _json.loads(vp_json)
        except ValueError:
            return
        self._cfg_store.set_viewport(self._vp_sig[0], self._vp_sig[1], vp)

    def request_quit(self):
        """退出入口：配了关闭动作就先播完再退，否则直接退。"""
        if self._state == "closing":
            return
        anim = self._pick_from("close")
        if anim and self._model:
            self._state = "closing"
            self._current_anim = anim
            self.set_animation(anim, loop=False)
            QTimer.singleShot(5000, QApplication.quit)  # 兜底：动画事件丢失也能退
        else:
            QTimer.singleShot(0, QApplication.quit)

    # ---- 拖动 / 缩放 ----
    def _on_drag(self, dx: int, dy: int):
        self.move(self.pos() + QPoint(dx, dy))

    def _on_wheel_zoom(self, dy: int):
        """滚轮缩放：上滚放大、下滚缩小，窗口中心保持不动。"""
        factor = _ZOOM_STEP if dy < 0 else 1 / _ZOOM_STEP
        scale = min(_SCALE_MAX, max(_SCALE_MIN, self._scale * factor))
        if scale == self._scale:
            return
        self._scale = scale
        self._apply_size()

    def _apply_size(self):
        """实际尺寸 = 基准 × 基础比例 × 滚轮缩放；以窗口中心为锚点不移位。"""
        size = int(_BASE_SIZE * self._base_scale * self._scale)
        center = self.frameGeometry().center()
        self.resize(size, size)
        self.move(center - self.rect().center())

    # ---- 点击穿透 ----
    def _set_through(self, on: bool):
        if self._through == on:
            return
        self._through = on
        hwnd = int(self.winId())
        ex = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        if on:
            ex |= _WS_EX_TRANSPARENT
        else:
            ex &= ~_WS_EX_TRANSPARENT
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex)

    def _point_on_character(self, lx: int, ly: int) -> bool:
        """窗口逻辑坐标 → 是否落在角色的可点击区域内。

        可点击区域 = 角色包围盒向中心收缩到 _HIT_SCALE 后的矩形
        ∩ 不透明像素（不再向外扩 1 格容差）。
        """
        if self._grid is None:
            return True  # 位图不可用（加载中）：整窗可交互
        w, h = self.width(), self.height()
        fx, fy = lx / w, ly / h
        l, t, r, b = self._char_bbox
        cx, cy = (l + r) / 2, (t + b) / 2
        hw, hh = (r - l) * _HIT_SCALE / 2, (b - t) * _HIT_SCALE / 2
        if not (cx - hw <= fx <= cx + hw and cy - hh <= fy <= cy + hh):
            return False
        gx, gy = int(fx * _GRID), int(fy * _GRID)
        return 0 <= gx < _GRID and 0 <= gy < _GRID \
            and self._grid[gy * _GRID + gx] == "1"

    def _poll_cursor(self):
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        g = self.frameGeometry()
        dpr = self.screen().devicePixelRatio()
        lx = int(pt.x / dpr - g.x())
        ly = int(pt.y / dpr - g.y())
        inside = self._point_on_character(lx, ly)
        lmb_down = bool(_user32.GetAsyncKeyState(0x01) & 0x8000)
        if self._through:
            if inside:
                self._set_through(False)
        else:
            # 光标离开角色且没按左键 → 恢复穿透
            if not inside and not lmb_down:
                self._set_through(True)

    # ---- 角色位图 ----
    def _update_hit_rect(self):
        # runJavaScript 的返回值数组无法正确转换，用字符串传回
        self.page().runJavaScript(
            "window._petMask ? (window._petMask() || '') : ''",
            self._apply_hit_rect)

    def _apply_hit_rect(self, s):
        if not s:
            # 位图不可用（页面未加载完）：整窗可交互
            self._hit_rect = self.rect()
            self._grid = None
            self._char_bbox = None
            return
        bbox, _, bits = str(s).partition(";")
        try:
            r = [float(x) for x in bbox.split(",")]
        except ValueError:
            return
        if len(r) != 4 or len(bits) != _GRID * _GRID:
            return
        self._grid = bits
        self._char_bbox = (r[0], r[1], r[2], r[3])  # 原始包围盒，供点击判定收缩用
        w, h = self.width(), self.height()
        l = max(0.0, r[0] - _MASK_PAD)
        t = max(0.0, r[1] - _MASK_PAD)
        rr = min(1.0, r[2] + _MASK_PAD)
        b = min(1.0, r[3] + _MASK_PAD)
        self._hit_rect = QRect(int(l * w), int(t * h),
                               int((rr - l) * w), int((b - t) * h))

    # ---- 右键菜单 ----
    main_window = None  # 由 main.py 注入，用于“隐藏主页面”

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        if self.main_window is not None and self.main_window.isVisible():
            act_open = QAction("隐藏主页面", self)
            act_open.triggered.connect(self.main_window.hide)
        else:
            act_open = QAction("打开主页面", self)
            act_open.triggered.connect(self.openRequested.emit)
        act_quit = QAction("退出 PRTS", self)
        # 配了关闭动作会播完再退；request_quit 自身带延迟，天然避开
        # menu.exec 嵌套事件循环吞 quit 的问题
        act_quit.triggered.connect(self.request_quit)
        act_restart = QAction("重新启动", self)
        act_restart.triggered.connect(self._restart)
        # 切换配置：列出已保存的搭配，点了直接换
        preset_menu = menu.addMenu("切换配置")
        names = self._cfg_store.presets()
        if not names:
            preset_menu.addAction("（还没有保存的搭配）").setEnabled(False)
        else:
            for name in names:
                preset_menu.addAction(name).triggered.connect(
                    lambda _=False, n=name: self._apply_preset(n))
        menu.addAction(act_open)
        menu.addAction(act_restart)
        menu.addAction(act_quit)
        menu.exec(e.globalPos())

    def _apply_preset(self, name: str):
        """切到指定搭配：走 PetPage 统一入口（落库 + 应用 + 界面同步）。"""
        mw = self.main_window
        if mw is not None:
            mw.pet_page.load_preset_by_name(name)
            return
        # 主页面还没建过（理论上不会）：直接自己换
        cfg = self._cfg_store.load_preset(name)
        if cfg:
            self._cfg_store.save(cfg)
            self.apply_config(cfg)

    def _restart(self):
        """重新启动：另起新进程（加载最新代码），本进程走正常退出流程。"""
        subprocess.Popen(
            [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]])
        self.request_quit()
