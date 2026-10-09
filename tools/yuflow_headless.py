"""YuFlow 无头运行器 —— 供 PRTS 调度器以子进程方式调用。

用法: python tools/yuflow_headless.py <flow文件路径>
行为对齐 YuFlow runner.py 的 _run_flow：读 YuFlow config.json，
连接目标窗口，执行工作流，日志走 stdout（由调用方重定向到日志文件）。
退出码: 0 成功 / 1 失败。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

YUFLOW_DIR = Path(
    os.environ.get(
        "YUFLOW_DIR",
        r"c:\Users\29087\Documents\Visual Studio 2022\Code Snippets\YuFlow",
    )
)


def main() -> int:
    if len(sys.argv) < 2:
        print("[错误] 用法: yuflow_headless.py <flow文件>")
        return 1
    flow_path = Path(sys.argv[1])
    if not flow_path.exists():
        print(f"[错误] flow 文件不存在: {flow_path}")
        return 1

    sys.path.insert(0, str(YUFLOW_DIR))
    os.chdir(YUFLOW_DIR)  # YuFlow 的相对资源（cache/image 等）以其根目录为基准

    import nodes  # noqa: F401 — 触发节点注册
    from engine.workflow import Workflow
    from engine.context import RuntimeContext
    from engine.executor import Executor

    config = json.loads((YUFLOW_DIR / "config.json").read_text(encoding="utf-8"))
    workflow = Workflow.load(flow_path)

    ctx = RuntimeContext(log_func=lambda msg: print(msg, flush=True))
    ctx.set("__api_url__", config.get("api_url", ""))
    ctx.set("__api_key__", config.get("api_key", ""))
    ctx.set("__model__", config.get("model", ""))

    # 连接目标窗口（对齐 runner.py 的实现）
    # 窗口名/输入方式以脚本 meta 为准（窗口跟着脚本走），config.json 兜底
    controller = None
    meta = workflow.meta or {}
    window_name = meta.get("window_name", "") or config.get("window_name", "")
    input_method_name = meta.get("input_method", "") or config.get(
        "input_method", "PostMessage")
    if window_name:
        try:
            from maa.toolkit import Toolkit
            from maa.controller import (
                Win32Controller,
                MaaWin32ScreencapMethodEnum,
                MaaWin32InputMethodEnum,
            )
            Toolkit.init_option(str(YUFLOW_DIR))
            input_method = getattr(
                MaaWin32InputMethodEnum,
                input_method_name,
                MaaWin32InputMethodEnum.PostMessage,
            )
            for w in Toolkit.find_desktop_windows():
                if window_name in w.window_name:
                    controller = Win32Controller(
                        hWnd=w.hwnd,
                        screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow,
                        mouse_method=input_method,
                        keyboard_method=input_method,
                    )
                    controller.post_connection().wait()
                    print(f"[窗口] 已连接: {w.window_name} ({input_method.name})", flush=True)
                    break
            if controller is None:
                print(f"[警告] 未找到窗口 '{window_name}'，无窗口模式运行", flush=True)
        except Exception as e:
            print(f"[警告] 窗口连接失败: {e}", flush=True)

    ctx.controller = controller
    ctx.set("__input_method__", input_method_name)

    print(f"[PRTS] 开始执行工作流: {workflow.name}", flush=True)
    result = Executor(workflow, ctx).run()
    ok = result.get("ok", False)
    print(f"[PRTS] 执行结束: {'成功' if ok else '失败'} {result.get('error', '')}",
          flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
