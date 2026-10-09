"""看板娘配置持久化：SQLite kv 表（data/prts.db）。

当前配置存 key="config"，命名搭配存 key="presets"（{名称: 配置}）。

一份配置 = 当前模型 + 各事件绑定的动画名列表。
事件：default（常态循环）/ start（启动）/ move（拖动中）/
interact（双击）/ close（退出前）/ idle（待机：每 10s 随机抽一个播一次）。
所有事件触发时都从列表随机挑一个播放；空列表 = 该事件不绑定动作。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.calendar_store import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pet_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_KEY = "config"
_PRESETS_KEY = "presets"

EVENTS = ("default", "start", "move", "interact", "close", "idle")

_DEFAULT: dict = {
    "model": "",
    "default": [], "start": [], "move": [], "interact": [],
    "close": [], "idle": [],
    "base_scale": 1.0,  # 基础比例：窗口尺寸 = 360 × base_scale × 滚轮缩放
}


def _normalize(cfg: dict) -> dict:
    """补全缺失键；事件字段统一为列表（兼容旧的单字符串格式）。"""
    out = dict(_DEFAULT)
    out.update(cfg or {})
    out.pop("char_id", None)  # 旧两级结构残留键，直接丢弃
    out.pop("skin_id", None)
    for key in EVENTS:
        v = out.get(key)
        if isinstance(v, str):
            out[key] = [v] if v else []
        elif not isinstance(v, list):
            out[key] = []
    try:
        out["base_scale"] = max(0.1, float(out.get("base_scale", 1.0)))
    except (TypeError, ValueError):
        out["base_scale"] = 1.0
    return out


class PetConfig:
    def __init__(self, db_path: Path = DB_PATH):
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)

    # ---- 当前配置 ----
    def load(self) -> dict:
        row = self._conn.execute(
            "SELECT value FROM pet_config WHERE key=?", (_KEY,)).fetchone()
        try:
            raw = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            raw = {}
        return _normalize(raw)

    def save(self, cfg: dict):
        self._put(_KEY, _normalize(cfg))

    # ---- 命名搭配 ----
    def presets(self) -> dict[str, dict]:
        row = self._conn.execute(
            "SELECT value FROM pet_config WHERE key=?", (_PRESETS_KEY,)).fetchone()
        try:
            raw = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            return {}
        return {name: _normalize(cfg) for name, cfg in raw.items()
                if isinstance(cfg, dict)}

    def save_preset(self, name: str, cfg: dict):
        ps = self.presets()
        ps[name] = _normalize(cfg)
        self._put(_PRESETS_KEY, ps)

    def delete_preset(self, name: str):
        ps = self.presets()
        if name in ps:
            del ps[name]
            self._put(_PRESETS_KEY, ps)

    def load_preset(self, name: str) -> dict | None:
        return self.presets().get(name)

    def _put(self, key: str, obj):
        self._conn.execute(
            "INSERT INTO pet_config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(obj, ensure_ascii=False)))
        self._conn.commit()

    # ---- 取景框缓存 ----
    # 并集取景计算要采样全部已配置动作（100 帧 × N 个动作），耗时 1~2 秒。
    # 结果只取决于 模型文件 + 动作集合，算一次存起来，启动直接套用。
    def get_viewport(self, model: str, sig: str) -> dict | None:
        row = self._conn.execute(
            "SELECT value FROM pet_config WHERE key=?",
            (f"viewport:{model}",)).fetchone()
        try:
            cached = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            return None
        return cached.get("vp") if cached.get("sig") == sig else None

    def set_viewport(self, model: str, sig: str, vp: dict):
        self._put(f"viewport:{model}", {"sig": sig, "vp": vp})

    @staticmethod
    def default_for(library, model: str) -> dict:
        """按骨架里的动画名猜一份合理默认配置。"""
        anims = (library.spine_info(model) or {}).get("animations", [])

        def pick(*keywords: str) -> list[str]:
            for kw in keywords:
                hit = [a for a in anims if kw.lower() in a.lower()]
                if hit:
                    return hit[:1]
            return []

        return _normalize({
            "model": model,
            "default": pick("Idle"),
            "start": pick("Start"),
            "move": pick("Move"),
            "interact": pick("Attack", "Interact", "Touch"),
            "close": pick("Die"),
            "idle": [],
        })

    # 一键配置的关键词规则：动画名的下划线分段里有匹配段即归入该事件
    # （用分段边界匹配，避免 Restart 误中 start）
    AUTO_RULES = {
        "default": "idle",
        "start": "start",
        "move": "move",
        "interact": "attack",
        "close": "die",
        "idle": "loop",
    }

    @classmethod
    def auto_for(cls, library, model: str) -> dict:
        """一键配置：按 AUTO_RULES 把骨架全部动画自动分配到各事件。"""
        import re
        anims = (library.spine_info(model) or {}).get("animations", [])
        cfg = {"model": model}
        for key in EVENTS:
            pat = re.compile(r"(?:^|_)" + cls.AUTO_RULES[key], re.IGNORECASE)
            cfg[key] = [a for a in anims if pat.search(a)]
        # 基建类骨架没有 Idle 系动画：默认动作兜底为 Default/Relax/第一个
        if not cfg["default"] and anims:
            cfg["default"] = [a for a in anims if a in ("Default", "Relax")][:1] \
                             or anims[:1]
        return _normalize(cfg)
