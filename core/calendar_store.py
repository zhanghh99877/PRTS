"""日历事项存储：SQLite。

事项（event）与 DDL（ddl）同表，kind 区分。
DDL 提醒策略：有时间的 DDL 在截止前 REMIND_AHEAD 分钟开始提醒；
无时间的按当天 09:00 处理。每条只提醒一次（notified 落库，重启不重复打扰）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from core.assets import PRTS_ROOT

DB_PATH = PRTS_ROOT / "data" / "prts.db"

REMIND_AHEAD_MIN = 60          # 截止前多久开始提醒
NO_TIME_DDL_HOUR = 9           # 全天 DDL 视为当天 9 点

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,            -- YYYY-MM-DD
    time     TEXT,                     -- HH:MM，NULL = 全天
    title    TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT 'event',  -- event | ddl
    done     INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
"""


class CalendarStore:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ---- 增删改 ----
    def add_event(self, date: str, title: str, kind: str = "event",
                  time: str | None = None, note: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO events(date, time, title, note, kind) VALUES (?,?,?,?,?)",
            (date, time, title, note, kind),
        )
        self._conn.commit()
        return cur.lastrowid

    def set_done(self, event_id: int, done: bool):
        self._conn.execute("UPDATE events SET done=? WHERE id=?",
                           (int(done), event_id))
        self._conn.commit()

    def update_event(self, event_id: int, title: str, kind: str,
                     time: str | None, note: str = ""):
        """改标题/类型/时间/详细内容（日期、完成状态不动）。"""
        self._conn.execute(
            "UPDATE events SET title=?, kind=?, time=?, note=? WHERE id=?",
            (title, kind, time, note, event_id))
        self._conn.commit()

    def move_event(self, event_id: int, new_date: str):
        """改期：移到另一天（标题/时间/详细内容/完成状态都不动）。"""
        self._conn.execute("UPDATE events SET date=? WHERE id=?",
                           (new_date, event_id))
        self._conn.commit()

    def delete(self, event_id: int):
        self._conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        self._conn.commit()

    # ---- 查询 ----
    def events_on(self, date: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE date=? ORDER BY done, time IS NULL, time, id",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, event_id: int) -> dict | None:
        """按 id 取单条事件（列表缓存失效后从数据库取最新用）。"""
        r = self._conn.execute("SELECT * FROM events WHERE id=?",
                               (event_id,)).fetchone()
        return dict(r) if r else None

    def month_marks(self, year: int, month: int) -> dict[str, dict]:
        """当月有事项的日期 → {has_event, has_pending_ddl, overdue_ddl}，供日历绘制。"""
        prefix = f"{year:04d}-{month:02d}-%"
        rows = self._conn.execute(
            "SELECT date, kind, done FROM events WHERE date LIKE ?", (prefix,)
        ).fetchall()
        today = datetime.now().date()
        marks: dict[str, dict] = {}
        for r in rows:
            m = marks.setdefault(r["date"], {"has_event": False,
                                             "has_pending_ddl": False,
                                             "overdue_ddl": False})
            if r["kind"] == "ddl" and not r["done"]:
                m["has_pending_ddl"] = True
                d = datetime.strptime(r["date"], "%Y-%m-%d").date()
                if d < today:
                    m["overdue_ddl"] = True
            else:
                m["has_event"] = True
        return marks

    # ---- 一览 ----
    def pending_overview(self, limit: int = 200) -> list[dict]:
        """所有未完成的事项与 DDL（含过期 DDL），按日期时间升序。"""
        rows = self._conn.execute(
            "SELECT * FROM events WHERE done=0 "
            "ORDER BY date, time IS NULL, time, id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- DDL 提醒 ----
    def due_reminders(self, now: datetime | None = None) -> list[dict]:
        """到点该提醒且还没提醒过的 DDL（含已过期未完成的）。"""
        now = now or datetime.now()
        rows = self._conn.execute(
            "SELECT * FROM events WHERE kind='ddl' AND done=0 AND notified=0"
        ).fetchall()
        due = []
        for r in rows:
            ddl_dt = datetime.strptime(
                f"{r['date']} {r['time'] or f'{NO_TIME_DDL_HOUR:02d}:00'}",
                "%Y-%m-%d %H:%M",
            )
            if now >= ddl_dt - timedelta(minutes=REMIND_AHEAD_MIN):
                e = dict(r)
                e["ddl_dt"] = ddl_dt
                due.append(e)
        return due

    def mark_notified(self, event_id: int):
        self._conn.execute("UPDATE events SET notified=1 WHERE id=?", (event_id,))
        self._conn.commit()

    def close(self):
        self._conn.close()
