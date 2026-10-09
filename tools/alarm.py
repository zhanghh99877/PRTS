"""最简单的闹钟：弹一个置顶窗口，15 秒后自动关闭。

专门用于测试 PRTS 触发器（手动/仅一次/定时都能测）。
日志会输出触发时刻，弹窗看到即说明脚本被成功拉起。
"""

import sys
import tkinter as tk
from datetime import datetime

msg = " ".join(sys.argv[1:]) or "闹钟时间到！"
now = datetime.now()
print(f"[alarm] 触发于 {now:%Y-%m-%d %H:%M:%S} —— {msg}", flush=True)

root = tk.Tk()
root.title("PRTS 闹钟")
root.attributes("-topmost", True)
tk.Label(root, text=msg, font=("Microsoft YaHei", 20), padx=40, pady=24).pack()
tk.Label(root, text=now.strftime("%H:%M:%S"),
         font=("Microsoft YaHei", 12), pady=8).pack()
root.after(15_000, root.destroy)  # 15 秒自动关闭，不阻塞任务状态
root.mainloop()
