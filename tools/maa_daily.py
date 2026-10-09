"""MAA 日常一条龙 —— 驱动 MAA 本体自动执行（供 PRTS 调度器子进程调用）。

链路：启动游戏（如未开）→ 启动 MAA.exe → MAA 按自身配置自动跑任务队列
→ 转发 MAA 日志到 stdout（PRTS 触发器收进任务日志）→ MAA 完成后自行退出 → 判定成败。

前置（一次性，在 MAA 界面里配好）：
1. 连接设置：连接方式选「窗口接管(AttachWindow)」，目标窗口「明日方舟」
2. 启动设置：勾「启动后直接开始任务」（否则 MAA 起来后停在主界面不跑）
3. 主界面：「完成后操作」选「退出 MAA」（否则脚本不知道何时算完）
任务队列（StartUp/Fight/Infrast 等）在 MAA 里排，本脚本不管细节。
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ===================== CONFIG =====================
MAA_DIR = Path(r"D:\Apps\MAA")
MAA_EXE = MAA_DIR / "MAA.exe"
GAME_EXE = Path(r"D:\Hypergryph Launcher\games\Arknights Game\Arknights.exe")
WINDOW_TITLE = "明日方舟"            # 游戏窗口标题（子串匹配）
MAA_LOG = MAA_DIR / "debug" / "gui.log"   # GUI 外部日志（面板同款，人话）
GAME_BOOT_TIMEOUT_S = 300            # 等游戏窗口出现的最长秒数
GAME_SETTLE_S = 15                   # 窗口出现后再等多久让游戏进登录界面
MAA_TIMEOUT_S = 3600                 # MAA 执行总时长上限
# ==================================================


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_game_hwnd() -> int:
    """枚举顶层窗口，标题含 WINDOW_TITLE 的第一个可见窗口（0 = 没找到）。"""
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @WNDENUMPROC
    def _enum(hwnd, _lp):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            if WINDOW_TITLE in buf.value:
                found.append(hwnd)
                return False
        return True

    ctypes.windll.user32.EnumWindows(_enum, None)
    return found[0] if found else 0


def ensure_game_running() -> bool:
    if find_game_hwnd():
        log("游戏窗口已存在")
        return True
    if not GAME_EXE.exists():
        log(f"[错误] 找不到游戏: {GAME_EXE}")
        return False
    log("启动明日方舟 ...")
    subprocess.Popen([str(GAME_EXE)], cwd=str(GAME_EXE.parent))
    deadline = time.time() + GAME_BOOT_TIMEOUT_S
    while time.time() < deadline:
        if find_game_hwnd():
            log(f"游戏窗口已出现，等待 {GAME_SETTLE_S}s 加载")
            time.sleep(GAME_SETTLE_S)
            return True
        time.sleep(5)
    log("[错误] 等待游戏窗口超时")
    return False


class LogTailer:
    """增量读 gui.log，只转发任务进度类日志。

    gui.log 里大部分是 GUI 内部流水（配置写入、版本检查、启动横幅等），
    规则：只认主日志通道 <2> 的行，再按噪声关键词黑名单过滤。
    """

    _DONE_MARK = "任务已全部完成！"
    _NOISE = (
        # 启动横幅 / 初始化
        "===", "GUI started", "Version v", "Built at", "Maa ENV",
        "Command Line", "User Dir", "Run as Administrator",
        "AsstSetUserDir", "Loading item list", "Failed to set startup",
        # 配置写入 / 版本检查 / HTTP
        "Configuration", "UpdateStageList", "HTTP:", ".json",
        "New version found", "resource latest version",
        "Failed to load configuration", "download urls", "mirror",
        "Allowing system to sleep", "Pending update", "Start to download",
        "Remove download temp", "Idle state confirmed", "url: ", "Delegat",
        # 运行框架流水
        "LinkStart", "Main windows log clear", "Build Time", "Idle:",
        "Blocking system from sleeping", "Whether the window placement",
        "Index ", "正在连接", "正在运行中",
        # 关闭流程
        "InterruptLock", "Shutdown called", "saved",
        "Waiting for save task", "GUI exited", "Post actions",
    )
    _NOISE_RE = re.compile(r"Index \d+, Type")  # 任务队列清单转储

    def __init__(self):
        self._offset = MAA_LOG.stat().st_size if MAA_LOG.exists() else 0
        self.all_completed = False

    def poll(self):
        if not MAA_LOG.exists():
            return
        with MAA_LOG.open(encoding="utf-8", errors="replace") as f:
            f.seek(self._offset)
            text = f.read()
            self._offset = f.tell()
        for line in text.splitlines():
            if self._DONE_MARK in line:
                self.all_completed = True
            if "<2>" not in line:      # 非主日志通道（<10> 等内部通道）
                continue
            if any(k in line for k in self._NOISE) or self._NOISE_RE.search(line):
                continue
            body = line.split("<2>", 1)[-1].strip()
            if body:
                log(f"[MAA] {body[:200]}")


def main() -> int:
    # 游戏以管理员身份运行；不提权则窗口接管/输入都会被 UIPI 拦截
    if not ctypes.windll.shell32.IsUserAnAdmin():
        log("[错误] 需要管理员权限：请用 PRTS_admin.bat 启动 PRTS，"
            "或以管理员身份运行本脚本")
        return 1
    if not MAA_EXE.exists():
        log(f"[错误] 找不到 MAA: {MAA_EXE}")
        return 1
    if not ensure_game_running():
        return 1

    tailer = LogTailer()
    log("启动 MAA ...")
    proc = subprocess.Popen([str(MAA_EXE)], cwd=str(MAA_DIR))
    deadline = time.time() + MAA_TIMEOUT_S
    while proc.poll() is None:
        if time.time() > deadline:
            log("[错误] MAA 执行超时，强制结束")
            proc.kill()
            return 1
        tailer.poll()
        time.sleep(3)
    tailer.poll()  # 收尾把剩余日志读完

    ok = tailer.all_completed
    log(f"MAA 已退出 (code={proc.returncode})，任务{'全部完成' if ok else '未全部完成'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
