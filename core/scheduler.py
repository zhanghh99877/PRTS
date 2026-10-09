"""触发器调度：任务存储 + 定时调度 + 子进程运行 + 日志。

任务类型：
- script：任意 Python 脚本（如 Kimi 模拟炒股触发器）
- yuflow：YuFlow 工作流文件，经 tools/yuflow_headless.py 无头执行

调度规则：
- interval：每 N 分钟（sched_param 为分钟数字符串）；从未运行过则启动后立即跑
- daily：一个或多个 HH:MM（sched_param 逗号分隔），可限定星期几
  （weekdays 列：空 = 每天，否则如 "0,2,4" = 周一/三/五）；
  当天到点且该时间点今天还没跑过就触发
- manual：手动触发，永不自动运行，只响应“立即运行”
- once：仅一次，sched_param 为 "YYYY-MM-DD HH:MM"，到点跑一次后自动禁用；
  消耗标记是 enabled 而非 last_run——手动“立即运行”不会消耗定时触发

自动触发确认：到点不直接运行，先发 trigger_pending 信号（UI 弹 5 秒
确认窗）；confirm_trigger 才真正启动，cancel_trigger 跳过本次
（interval/daily 等下一周期，once 直接禁用）。

过期补跑（catchup 字段，仅影响 once / daily）：
- 开：app 没在运行而错过的触发，启动后补跑；
  可用 catchup_window（分钟，0=不限）限制最大补跑范围，
  超过时限的 once 视为错过（自动禁用）、daily 跳过该时间点
- 关：超过宽限期（CATCHUP_GRACE）视为错过——daily 等下一次，once 自动禁用

日志：logs/<id>_<任务名>/<yyyymmdd>.log，stdout/stderr 合并追加。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from core.assets import PRTS_ROOT
from core.calendar_store import CalendarStore

LOGS_DIR = PRTS_ROOT / "logs"
MAIN_LOG = LOGS_DIR / "main.log"  # 主日志：只记“什么时候触发了什么”，详情看各任务日志
TICK_MS = 15_000
CATCHUP_GRACE = timedelta(minutes=5)  # 过期宽限期：超过才算“错过”

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,              -- script | yuflow
    target      TEXT NOT NULL,              -- 脚本/flow 文件路径
    sched_kind  TEXT NOT NULL,              -- interval | daily | manual | once
    sched_param TEXT NOT NULL,              -- 分钟数 | HH:MM[,HH:MM...]
    weekdays    TEXT NOT NULL DEFAULT '',   -- daily 限定星期：空=每天，"0,2,4"=周一三五
    catchup_window INTEGER NOT NULL DEFAULT 0,  -- 补跑时限（分钟，0=不限）
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run    TEXT,
    last_status TEXT                        -- ok | failed | stopped | running
);
"""


class TaskStore:
    """tasks 表存取，与日历共用一个数据库文件。"""

    def __init__(self, cal_store: CalendarStore):
        self._conn = cal_store._conn
        self._conn.executescript(_SCHEMA)
        # 老库迁移：补 catchup / weekdays / catchup_window 列
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(tasks)")]
        if "catchup" not in cols:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN catchup INTEGER NOT NULL DEFAULT 1")
            self._conn.commit()
        if "weekdays" not in cols:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN weekdays TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        if "catchup_window" not in cols:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN catchup_window INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()

    def add(self, name, type_, target, sched_kind, sched_param,
            enabled=True, catchup=True, weekdays="", catchup_window=0) -> int:
        cur = self._conn.execute(
            "INSERT INTO tasks(name,type,target,sched_kind,sched_param,enabled,"
            "catchup,weekdays,catchup_window) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, type_, target, sched_kind, sched_param, int(enabled),
             int(catchup), weekdays, int(catchup_window)),
        )
        self._conn.commit()
        return cur.lastrowid

    def update(self, task_id, name, type_, target, sched_kind, sched_param,
               enabled, catchup, weekdays="", catchup_window=0):
        self._conn.execute(
            "UPDATE tasks SET name=?,type=?,target=?,sched_kind=?,sched_param=?,"
            "enabled=?,catchup=?,weekdays=?,catchup_window=? WHERE id=?",
            (name, type_, target, sched_kind, sched_param, int(enabled),
             int(catchup), weekdays, int(catchup_window), task_id),
        )
        self._conn.commit()

    def set_enabled(self, task_id, enabled: bool):
        self._conn.execute("UPDATE tasks SET enabled=? WHERE id=?",
                           (int(enabled), task_id))
        self._conn.commit()

    def set_run_state(self, task_id, last_run: str | None, status: str):
        if last_run is not None:
            self._conn.execute(
                "UPDATE tasks SET last_run=?, last_status=? WHERE id=?",
                (last_run, status, task_id))
        else:
            self._conn.execute(
                "UPDATE tasks SET last_status=? WHERE id=?", (status, task_id))
        self._conn.commit()

    def delete(self, task_id):
        self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self._conn.commit()

    def all(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get(self, task_id) -> dict | None:
        r = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None


class Scheduler(QObject):
    """定时扫描到期任务并用 QProcess 运行。"""

    changed = Signal()  # 任务表内容或状态变化
    # 自动触发到点：(task_id, 任务名)。不立即运行，先发信号弹确认窗，
    # 由 confirm_trigger / cancel_trigger 决定执行还是跳过
    trigger_pending = Signal(int, str)

    def __init__(self, store: TaskStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._procs: dict[int, QProcess] = {}
        self._pending: set[int] = set()  # 已弹确认窗、等待确认/取消的自动触发
        LOGS_DIR.mkdir(exist_ok=True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.start(TICK_MS)

    # ---- 调度 ----
    def tick(self):
        now = datetime.now()
        for t in self.store.all():
            if not t["enabled"] or t["id"] in self._procs:
                continue
            try:
                if self._is_due(t, now):
                    if t["id"] not in self._pending:
                        # 到点不直接跑：先弹确认窗，倒计时结束才启动
                        self._pending.add(t["id"])
                        self.trigger_pending.emit(t["id"], t["name"])
                elif self._is_missed(t, now):
                    # 仅一次 + 不补跑 + 已过期 → 标记错过（禁用）
                    self.store.set_enabled(t["id"], False)
                    self.changed.emit()
            except (ValueError, KeyError) as e:
                # 单个任务参数损坏不能拖垮整个调度器
                print(f"[scheduler] 任务 {t.get('id')} 参数异常，已跳过: {e}",
                      file=sys.stderr)

    @staticmethod
    def _is_due(t: dict, now: datetime) -> bool:
        catchup = bool(t.get("catchup", 1))
        # 补跑时限（分钟，0=不限）：错过的触发超过该时长就不补了
        window_min = int(t.get("catchup_window", 0) or 0)
        window = timedelta(minutes=window_min) if window_min > 0 else None

        def catchable(late: timedelta) -> bool:
            """错过 late 时长的触发是否还应执行。"""
            if not catchup:
                return late <= CATCHUP_GRACE
            return window is None or late <= window

        if t["sched_kind"] == "manual":
            return False
        if t["sched_kind"] == "once":
            # 以 enabled 为唯一消耗标记：手动“立即运行”不消耗定时触发
            target = datetime.strptime(t["sched_param"], "%Y-%m-%d %H:%M")
            return now >= target and catchable(now - target)
        last = datetime.fromisoformat(t["last_run"]) if t["last_run"] else None
        if t["sched_kind"] == "interval":
            if last is None:
                return True  # 从未运行：启用后立刻跑第一次
            return now - last >= timedelta(minutes=int(t["sched_param"]))
        # daily：多个 HH:MM（逗号分隔），可限定星期几；任一时间点到点即触发
        wd = (t.get("weekdays") or "").strip()
        if wd and str(now.weekday()) not in {x.strip() for x in wd.split(",")}:
            return False
        for tok in t["sched_param"].split(","):
            hh, mm = map(int, tok.strip().split(":"))
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now >= due and (last is None or last < due) and catchable(now - due):
                return True
        return False

    @staticmethod
    def _is_missed(t: dict, now: datetime) -> bool:
        """仅一次任务错过：不补跑 + 过宽限期，或补跑 + 超补跑时限。"""
        if t["sched_kind"] != "once":
            return False
        target = datetime.strptime(t["sched_param"], "%Y-%m-%d %H:%M")
        late = now - target
        if not t.get("catchup", 1):
            return late > CATCHUP_GRACE
        window_min = int(t.get("catchup_window", 0) or 0)
        return window_min > 0 and late > timedelta(minutes=window_min)

    # ---- 自动触发确认 ----
    def confirm_trigger(self, task_id: int):
        """确认窗倒计时结束（未取消）：真正启动任务。"""
        if task_id not in self._pending:
            return
        self._pending.discard(task_id)
        t = self.store.get(task_id)
        if not t or not t["enabled"]:
            return
        self.run_task(task_id)
        if t["sched_kind"] == "once":
            # 仅一次：跑完自动禁用
            self.store.set_enabled(task_id, False)
            self.changed.emit()

    def cancel_trigger(self, task_id: int):
        """确认窗点「取消」：跳过这次自动触发。

        interval/daily 把 last_run 记为现在（等下一周期/时间点）；
        once 视为已消耗，直接禁用。
        """
        if task_id not in self._pending:
            return
        self._pending.discard(task_id)
        t = self.store.get(task_id)
        if not t:
            return
        self._main_log(f"取消「{t['name']}」(自动触发被用户取消)")
        if t["sched_kind"] == "once":
            self.store.set_enabled(task_id, False)
        else:
            self.store.set_run_state(
                task_id, datetime.now().isoformat(timespec="seconds"), "cancelled")
        self.changed.emit()

    # ---- 运行 ----
    def _main_log(self, text: str):
        """主日志：一行一条，只记触发/结束概要。"""
        LOGS_DIR.mkdir(exist_ok=True)
        with MAIN_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")

    def run_task(self, task_id: int, manual: bool = False):
        self._pending.discard(task_id)  # 手动“立即运行”同时清掉挂起的确认窗
        t = self.store.get(task_id)
        if not t or task_id in self._procs:
            return
        target = str(Path(t["target"]).resolve())  # 统一绝对路径，避免 cwd 拼接歧义
        log = self.log_path(t)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 开始运行 =====\n")
        self._main_log(
            f"触发「{t['name']}」({'手动' if manual else '自动'}, {t['type']})")

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setStandardOutputFile(str(log), QProcess.OpenModeFlag.Append)
        # 子进程强制 UTF-8 输出，否则 Windows 下中文是 GBK，日志无法按 UTF-8 读
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        proc.setProcessEnvironment(env)
        if t["type"] == "yuflow":
            proc.setProgram(sys.executable)
            proc.setArguments([str(PRTS_ROOT / "tools" / "yuflow_headless.py"), target])
            proc.setWorkingDirectory(str(PRTS_ROOT))
        else:
            proc.setProgram(sys.executable)
            proc.setArguments([target])
            proc.setWorkingDirectory(str(Path(target).parent))

        proc.finished.connect(
            lambda code, _s, tid=task_id: self._on_finished(tid, code)
        )
        self._procs[task_id] = proc
        self.store.set_run_state(task_id, datetime.now().isoformat(timespec="seconds"),
                                 "running")
        proc.start()
        self.changed.emit()

    def stop_task(self, task_id: int):
        proc = self._procs.get(task_id)
        if proc:
            proc.kill()  # finished 信号里会记 stopped

    def is_running(self, task_id: int) -> bool:
        return task_id in self._procs

    def _on_finished(self, task_id: int, exit_code: int):
        proc = self._procs.pop(task_id, None)
        if proc is None:
            return
        try:
            killed = proc.exitStatus() == QProcess.ExitStatus.CrashExit
        except RuntimeError:
            killed = False  # 进程对象已在事件循环外被销毁
        status = "stopped" if killed else ("ok" if exit_code == 0 else "failed")
        self.store.set_run_state(task_id, None, status)
        t = self.store.get(task_id)
        name = t["name"] if t else f"#{task_id}"
        self._main_log(f"结束「{name}」→ {status}")
        if t is not None:  # 运行中被删除的任务不再写分任务日志
            log = self.log_path(t)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"===== {datetime.now():%H:%M:%S} 结束，状态 {status} =====\n")
        try:
            proc.deleteLater()  # 任务跑完即释放 QProcess，避免常驻堆积
        except RuntimeError:
            pass  # 应用退出流程中对象可能已被销毁
        self.changed.emit()

    # ---- 日志 ----
    @staticmethod
    def log_dir(t: dict) -> Path:
        safe = re.sub(r'[\\/:*?"<>|]', "_", t["name"])
        return LOGS_DIR / f"{t['id']}_{safe}"

    @classmethod
    def log_path(cls, t: dict, day: datetime | None = None) -> Path:
        day = day or datetime.now()
        return cls.log_dir(t) / f"{day:%Y%m%d}.log"

    @classmethod
    def log_files(cls, t: dict) -> list[Path]:
        d = cls.log_dir(t)
        if not d.exists():
            return []
        return sorted(d.glob("*.log"), reverse=True)
