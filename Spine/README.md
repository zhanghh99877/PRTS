# Spine —— 模型库

每个子文件夹 = 一套「角色皮肤」，名称取文件夹名。

## 添加新模型

把三件套放进一个文件夹即可，启动时自动识别：

- `*.skel` + `*.atlas` + atlas 引用的 `.png`（文件名随意，atlas 里写的是谁就认谁）
- 贴图尺寸必须与 atlas 声明的 `size:` 一致；不一致会在启动时自动等比缩放校正（原图备份为 `.bak`）
- 动画列表自动从 `.skel` 提取，无需登记

## 不识别的情况

- 三件套不齐（缺 skel / atlas / png 任意一个）
- `vendor/` 目录（spine-player 库，勿动；含两处 [PRTS patch]：HiDPI 渲染 + preserveDrawingBuffer）
