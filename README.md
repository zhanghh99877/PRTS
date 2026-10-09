# PRTS

一个明日方舟桌宠软件。

基于 PySide6 + Spine 的桌面看板：悬浮窗桌宠 + 日历/DDL 提醒 + 定时触发器，系统托盘常驻。

## 功能

- **桌宠**：明日方舟角色 Spine 动画悬浮窗，支持拖动、双击互动、待机动作、滚动缩放，可自定义各事件触发的动画与缩放。
- **日历**：月历视图上标记事项与 DDL，截止前自动托盘弹窗提醒
- **触发器**：定时/条件任务，到点右下角弹窗确认后执行自定义脚本
- **命名搭配**：保存多套看板配置，一键切换

## 安装

需要 **Python 3.10+**（推荐 3.12）。

```bash
# 克隆仓库
git clone https://github.com/zhanghh99877/arknights-desktop-pet.git
cd arknights-desktop-pet

# 安装依赖
pip install -r requirements.txt
```

国内网络如果装不上 PySide6，可以用镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 启动

```bash
python main.py
```

启动后桌宠出现在桌面上，主窗口通过托盘图标或右键桌宠打开。

> 程序是单实例的：重复运行会让旧实例退出、新实例接管。

## 目录结构

```
main.py            # 入口：托盘 + 单实例 + DDL 提醒
core/              # 桌宠渲染、配置存储、定时调度、日历存储（SQLite）
ui/                # 主窗口、日历页、触发器页、桌宠窗口
Spine/             # 角色 Spine 资源（skel/atlas/png）+ spine-player 运行时
Title/             # 窗口图标
tools/             # 触发器可调用的小工具脚本
data/              # 运行时自动生成的数据库（gitignore，无需手动创建）
```

## 添加新角色

在Spine文件夹中有几个下载好的文件。
在PRTS页面中查看“导入角色模型”，按F12控制台的“网络”页面获取Spine图形，下载一套 `.skel`（骨骼） / `.atlas`（贴图方式） / `.png`（贴图） 文件。请把 Spine 导出的 `.skel` / `.atlas` / `.png` 三件套放到 `Spine/` 下建立新文件夹里，文件夹命名为角色名，重启软件后在「看板」页可以选择该文件夹名称。
**由于部分动作占位过大，部分角色的基础显示比例过小，需要在基础比例处自行修改。