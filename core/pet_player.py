"""看板渲染容器：Spine 骨架实时渲染（spine-player 3.8，Spine/vendor/）。

交互走 JS + QWebChannel（petBridge）：Chromium 渲染层会吞掉部分
系统级鼠标事件，Qt 侧事件过滤器不可靠；JS 监听器两种输入都能收到。

JS 桥事件：单击 / 双击 / 拖动开始 / 拖动位移 / 拖动结束 / 滚轮 / 动画播完。
页面还暴露 window._petMask()：把 WebGL 画面降采样成 45x45 alpha 位图，
供 Qt 侧做逐像素点击穿透（依赖 vendor 补丁 preserveDrawingBuffer）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from core.assets import PRTS_ROOT

VENDOR = PRTS_ROOT / "Spine" / "vendor" / "spine-player"

# 切换动作时的交叉淡化时长（秒），0 = 硬切
_MIX_SECONDS = 0.2

# 交互桥：JS 鼠标/动画事件 → Qt 信号
_BRIDGE_JS = """
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
  new QWebChannel(qt.webChannelTransport, ch => {
    window.bridge = ch.objects.petBridge;
  });
  let _pd = null, _moved = false, _clickTimer = null;
  document.addEventListener('mousedown', e => {
    if (e.button === 0) { _pd = [e.screenX, e.screenY]; _moved = false; }
  });
  document.addEventListener('mousemove', e => {
    if (!_pd) return;
    const dx = e.screenX - _pd[0], dy = e.screenY - _pd[1];
    if (_moved || Math.abs(dx) + Math.abs(dy) > 8) {
      if (!_moved && window.bridge) window.bridge.dragStart();
      _moved = true;
      if (window.bridge) window.bridge.dragBy(dx, dy);
      _pd = [e.screenX, e.screenY];
    }
  });
  document.addEventListener('mouseup', e => {
    if (e.button !== 0 || !_pd) return;
    _pd = null;
    if (_moved) {
      if (window.bridge) window.bridge.dragEnd();
      return;
    }
    // 单击/双击消歧：260ms 内没有第二击才算单击
    if (_clickTimer) {
      clearTimeout(_clickTimer);
      _clickTimer = null;
      if (window.bridge) window.bridge.doubleClicked();
    } else {
      _clickTimer = setTimeout(() => {
        _clickTimer = null;
        if (window.bridge) window.bridge.clicked();
      }, 260);
    }
  });
  document.addEventListener('wheel', e => {
    if (window.bridge) window.bridge.wheeled(Math.trunc(e.deltaY));
  }, {passive: true});
</script>"""

# 点击穿透位图：把 WebGL 画面缩到 45x45 读 alpha，
# 返回 "l,t,r,b;45x45位图(0/1)"（与 Qt 侧 _apply_hit_rect 约定一致）
_MASK_JS = """
<script>
  window._petMask = function() {
    try {
      const c = document.querySelector('canvas');
      if (!c || !window.player || !window.player.loaded) return null;
      const S = 45;
      if (!window._mc) {
        window._mc = document.createElement('canvas');
        window._mc.width = S; window._mc.height = S;
        window._mg = window._mc.getContext('2d', {willReadFrequently: true});
      }
      const g = window._mg;
      g.clearRect(0, 0, S, S);
      g.drawImage(c, 0, 0, S, S);
      const d = g.getImageData(0, 0, S, S).data;
      let x0 = S, y0 = S, x1 = -1, y1 = -1, bits = '';
      for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
        const opaque = d[(y * S + x) * 4 + 3] > 16;
        bits += opaque ? '1' : '0';
        if (opaque) {
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
      if (x1 < 0) return null;
      return [x0 / S, y0 / S, (x1 + 1) / S, (y1 + 1) / S].join(',') + ';' + bits;
    } catch (e) { return null; }
  };
</script>"""

_SPINE_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="{vendor}/spine-player.css">
  <style>
    html, body {{ margin: 0; padding: 0; background: {bg}; overflow: hidden; }}
    #player {{ width: 100vw; height: 100vh; }}
    .spine-player {{ background: transparent !important; }}
  </style>
</head>
<body>
  <div id="player"></div>
  <script src="{vendor}/spine-core.js"></script>
  <script src="{vendor}/spine-webgl.js"></script>
  <script src="{vendor}/spine-player.js"></script>
  <script>
    window.player = new spine.SpinePlayer(document.getElementById('player'), {{
      skelUrl: '{skel}',
      atlasUrl: '{atlas}',
      // 取景框只在加载时按 fit 动作（常态动作）算一次，之后永不重算：
      // 若按 Start 这类大范围动作取景，主模型会被压缩显示
      animation: '{fit}',
      alpha: true,
      backgroundColor: '#00000000',
      showControls: false,
      success: function (player) {{
        // 动作切换交叉淡化，避免硬切
        player.animationState.data.defaultMix = {mix};
        // 非循环动画播完 → 通知 Qt 切回默认
        player.animationState.addListener({{ complete: function (entry) {{
          if (!entry.loop && window.bridge)
            window.bridge.animComplete(entry.animation.name);
        }} }});
        // 取景框 = 全部已配置动作包围盒的并集（封顶 fit 动作的 5 倍），
        // 之后切换动作不再重算：模型大小恒定，大动作也播得下。
        // 计算结果由 Qt 侧缓存：有缓存直接套用，跳过采样
        var uvp = {cached_vp};
        var names = {fit_all};
        if (!uvp && names.length && player.currentViewport) {{
          var fv = player.calculateAnimationViewport('{fit}');
          var cx = fv.x + fv.width / 2, cy = fv.y + fv.height / 2;
          var maxW = fv.width * 5, maxH = fv.height * 5;
          var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
          names.push('{fit}');
          for (var i = 0; i < names.length; i++) {{
            try {{
              var av = player.calculateAnimationViewport(names[i]);
              x0 = Math.min(x0, av.x); y0 = Math.min(y0, av.y);
              x1 = Math.max(x1, av.x + av.width); y1 = Math.max(y1, av.y + av.height);
            }} catch (e) {{}}
          }}
          x0 = Math.max(x0, cx - maxW / 2); x1 = Math.min(x1, cx + maxW / 2);
          y0 = Math.max(y0, cy - maxH / 2); y1 = Math.min(y1, cy + maxH / 2);
          uvp = {{ x: x0, y: y0, width: x1 - x0, height: y1 - y0,
                  padLeft: '10%', padRight: '10%',
                  padTop: '10%', padBottom: '10%' }};
          uvp.padLeft = player.percentageToWorldUnit(uvp.width, uvp.padLeft);
          uvp.padRight = player.percentageToWorldUnit(uvp.width, uvp.padRight);
          uvp.padTop = player.percentageToWorldUnit(uvp.height, uvp.padTop);
          uvp.padBottom = player.percentageToWorldUnit(uvp.height, uvp.padBottom);
          if (window.bridge) window.bridge.cacheViewport(JSON.stringify(uvp));
        }}
        if (uvp) {{
          player.currentViewport = uvp;
          player.previousViewport = uvp;  // 不要初始过渡动画
          player.viewportTransitionStart = 0;
        }}
        // 首个动作（如启动）必须最后设置：
        // calculateAnimationViewport 内部会 clearTracks()，先设会被并集计算清掉
        var anim = '{animation}';
        if (anim) player.animationState.setAnimation(0, anim, {loop});
        // 显示缩放：view_scale=0.5 → 取景框以中心放大一倍，动画显示为一半
        var vs = {view_scale};
        if (vs > 0 && vs !== 1 && player.currentViewport) {{
          var vp = player.currentViewport, k = 1 / vs;
          vp.x -= vp.width * (k - 1) / 2;
          vp.y -= vp.height * (k - 1) / 2;
          vp.width *= k;
          vp.height *= k;
        }}
      }},
    }});
  </script>
  {mask}
  {bridge}
</body>
</html>"""


class PetBridge(QObject):
    """JS → Qt 交互桥。JS 调 clicked / doubleClicked / dragStart /
    dragBy / dragEnd / wheeled / animComplete。"""

    petClicked = Signal()
    petDoubleClicked = Signal()
    dragStarted = Signal()
    dragDelta = Signal(int, int)
    dragEnded = Signal()
    wheelDelta = Signal(int)      # 滚轮 deltaY：负值向上滚（放大），正值向下滚（缩小）
    animCompleted = Signal(str)   # 非循环动画播完（动画名）
    viewportComputed = Signal(str)  # 并集取景框算出来了（JSON），Qt 侧缓存用

    @Slot()
    def clicked(self):
        self.petClicked.emit()

    @Slot()
    def doubleClicked(self):
        self.petDoubleClicked.emit()

    @Slot()
    def dragStart(self):
        self.dragStarted.emit()

    @Slot(int, int)
    def dragBy(self, dx: int, dy: int):
        self.dragDelta.emit(dx, dy)

    @Slot()
    def dragEnd(self):
        self.dragEnded.emit()

    @Slot(int)
    def wheeled(self, dy: int):
        self.wheelDelta.emit(dy)

    @Slot(str)
    def animComplete(self, name: str):
        self.animCompleted.emit(name)

    @Slot(str)
    def cacheViewport(self, vp_json: str):
        self.viewportComputed.emit(vp_json)


class PetPlayer(QWebEngineView):
    """看板渲染器：加载 spine 骨架并播放动作。

    background: "transparent" 表示穿透到底层窗口（悬浮窗用），
    也可传 CSS 颜色（内嵌展示区用，如 "#ffffff"）。
    bridge: JS 交互桥（见 PetBridge 信号）。
    """

    def __init__(self, background: str = "transparent", parent=None):
        super().__init__(parent)
        self._background = background
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )
        if background == "transparent":
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.bridge = PetBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("petBridge", self.bridge)
        self.page().setWebChannel(self._channel)

    def _set_bg(self):
        self.page().setBackgroundColor(
            QColor(0, 0, 0, 0) if self._background == "transparent"
            else QColor(self._background)
        )

    def play_spine(self, skel: Path, atlas: Path, animation: str = "",
                   loop: bool = True, fit: str = "", view_scale: float = 1.0,
                   fit_all: list = (), viewport: dict | None = None):
        """整套加载 spine 骨架（切模型时用）。

        animation 为首个播放的骨架内动画名；fit 为取景参照动作
        （取景框只在加载时计算一次，之后切换动作不再重算，
        保证模型显示大小恒定）。fit 缺省取 animation。
        fit_all：参与取景并集的全部动作名（缺省只有 fit）。
        并集尺寸封顶为 fit 包围盒的 5 倍，防止离谱动作把主体缩太小。
        viewport：缓存的并集取景框（由 viewportComputed 信号回传后落库），
        传入则跳过采样直接套用。
        view_scale 为显示缩放：0.5 = 动画显示为取景框的一半大小（预览用）。
        """
        import json
        self._set_bg()
        self.setHtml(
            _SPINE_HTML.format(
                vendor=VENDOR.as_uri(), bg=self._background,
                skel=skel.as_uri(), atlas=atlas.as_uri(), animation=animation,
                fit=fit or animation,
                loop='true' if loop else 'false',
                view_scale=view_scale,
                fit_all=json.dumps(list(fit_all)),
                cached_vp=json.dumps(viewport) if viewport else 'null',
                mix=_MIX_SECONDS, mask=_MASK_JS, bridge=_BRIDGE_JS),
            QUrl.fromLocalFile(str(PRTS_ROOT) + "/"),
        )

    def set_animation(self, animation: str, loop: bool = True):
        """同骨架内切换动作（不重载页面、不重算取景框，交叉淡化）。"""
        safe = animation.replace("'", "\\'")
        self.page().runJavaScript(
            f"window.player && window.player.animationState"
            f".setAnimation(0, '{safe}', {'true' if loop else 'false'});"
        )

    def stop(self):
        self.setHtml("<html><body style='background:transparent'></body></html>")
