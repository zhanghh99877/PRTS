"""Spine 资产库：文件夹扫描自动识别 spine 模型，无需手工登记。

识别规则：Spine/ 下任意文件夹（任意深度，vendor 除外）凑齐
.skel / .atlas / .png 三件套，即为一套「角色皮肤」，名称取文件夹名。
动画名列表从 .skel 二进制直接提取（长度前缀字符串 + 命名前缀过滤，
对 Arknights 导出命名习惯验证过与运行时枚举一致）。
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

PRTS_ROOT = Path(__file__).resolve().parent.parent
RELAY_DIR = PRTS_ROOT / "Spine"
TITLE_DIR = PRTS_ROOT / "Title"

_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# 动画名常见前缀（过滤骨骼/插槽/皮肤/事件等同格式字符串）。
# 必须大小写敏感：Arknights 动画名是大写开头（Default/Idle/Skill_1_Loop），
# 事件名是小写（skill_storage_w）， IGNORECASE 会把事件名误判成动画
_ANIM_PREFIX = re.compile(
    r"^(Start|Idle|Die|Attack|Skill|Default|Move|Begin|End|Loop|Combat"
    r"|Takeoff|Restart|Sit|Sleep|Talk|Walk|Run|Jump|Special|Interact"
    r"|Touch|Relax)")


def extract_animation_names(skel: Path) -> list[str]:
    """从 spine 3.8 二进制 skel 提取动画名（保声明序，去重）。"""
    data = skel.read_bytes()
    seen, out = set(), []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        i += 1
        # spine 字符串编码：varint 长度前缀，短名即单字节 len+1
        if not (2 <= b <= 66) or i + b - 1 > n:
            continue
        size = b - 1
        try:
            s = data[i:i + size].decode("ascii")
        except UnicodeDecodeError:
            continue
        if _IDENT.fullmatch(s) and _ANIM_PREFIX.match(s) and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def title_path() -> Path | None:
    """应用图标：Title/title.png（或 title.ico），直接放一张图即可。"""
    for name in ("title.png", "title.ico"):
        p = TITLE_DIR / name
        if p.exists():
            return p
    return None


def _atlas_texture(folder: Path, atlas: Path) -> Path | None:
    """atlas 引用的贴图：取首页声明的文件名精确定位（避免把 title.png 当贴图）。"""
    try:
        with atlas.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    p = folder / line
                    if p.suffix.lower() == ".png" and p.exists():
                        return p
                    break
    except OSError:
        pass
    # 兜底：任意非 title 的 png
    return next((p for p in folder.glob("*.png") if p.stem != "title"), None)


def _png_size(png: Path) -> tuple[int, int] | None:
    """读 PNG 头（IHDR）拿尺寸，失败返回 None。"""
    try:
        with png.open("rb") as f:
            d = f.read(33)
        if d[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", d[16:24])
    except OSError:
        return None


def ensure_texture_matches_atlas(atlas: Path, png: Path):
    """保证贴图尺寸 == atlas 声明的 size，不符则等比缩放到声明尺寸。

    spine 运行时用贴图真实尺寸计算 UV（spine-core.js 里 page.width 被
    texture 图片尺寸覆盖），尺寸不符会导致整个模型拼装错乱。
    缩放前把原图备份为 <文件名>.bak（已存在则跳过备份）。
    """
    declared = None
    try:
        with atlas.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("size:"):
                    w, h = line[5:].strip().split(",")
                    declared = (int(w), int(h))
                    break
    except (OSError, ValueError):
        return
    actual = _png_size(png)
    if not declared or not actual or declared == actual:
        return
    try:
        from PySide6.QtGui import QImage
        from PySide6.QtCore import Qt
        img = QImage(str(png))
        if img.isNull():
            return
        bak = png.with_suffix(png.suffix + ".bak")
        if not bak.exists():
            import shutil
            shutil.copy2(png, bak)  # 复制备份，缩放保存失败也不丢原图
        scaled = img.scaled(*declared,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        scaled.save(str(png))
        print(f"[assets] {png.name}: 贴图 {actual} ≠ atlas 声明 {declared}"
              f"，已等比缩放（原图备份为 {bak.name}）")
    except Exception as e:  # 兜底失败不阻断扫描
        print(f"[assets] {png.name}: 贴图尺寸校正失败: {e}")


class RelayLibrary:
    """Spine 目录的只读视图：自动发现的模型集合。"""

    def __init__(self, root: Path = RELAY_DIR):
        self._models: dict[str, dict] = {}
        skels = [p for p in root.rglob("*.skel")
                 if "vendor" not in p.parts]
        for folder in sorted({p.parent for p in skels}):
            skel = next(folder.glob("*.skel"), None)
            atlas = next(folder.glob("*.atlas"), None)
            png = _atlas_texture(folder, atlas) if atlas else None
            if not (skel and atlas and png):
                continue  # 三件套不齐，不识别为模型
            ensure_texture_matches_atlas(atlas, png)
            self._models[folder.name] = {
                "skel": skel, "atlas": atlas,
                "animations": extract_animation_names(skel),
            }

    def models(self) -> list[str]:
        """全部模型名（文件夹名，按名称排序）。"""
        return sorted(self._models)

    def spine_info(self, model: str) -> dict | None:
        """模型资产：{"skel": Path, "atlas": Path, "animations": [动画名]}。"""
        return self._models.get(model)

    def first_spine(self) -> str | None:
        """默认模型名。"""
        models = self.models()
        return models[0] if models else None
