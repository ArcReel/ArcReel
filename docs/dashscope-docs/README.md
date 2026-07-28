# 阿里百炼（DashScope / Model Studio）文档汇总

本目录是为 ArcReel 集成阿里百炼供应商（issue #673）汇总的 **一手核实** 资料,供实现期查阅。

## 索引

- [API 概览.md](./API%20概览.md) — Base URL、鉴权、异步任务两步式调用、轮询、状态机
- [千问-文生图.md](./千问-文生图.md) — `qwen-image-2.0-pro` / `qwen-image-2.0`(融合 T2I+I2I)+ 经典 `qwen-image-max/plus/image` 系列
- [千问-图像编辑.md](./千问-图像编辑.md) — `qwen-image-edit-max` / `qwen-image-edit-plus` / `qwen-image-edit` 编辑专用模型
- [万相2.7-图像.md](./万相2.7-图像.md) — `wan2.7-image` / `wan2.7-image-pro`:T2I/编辑/组图/交互式编辑(bbox)/思考模式/4K
- [参考生视频-HappyHorse.md](./参考生视频-HappyHorse.md) — `happyhorse-1.0-r2v` 请求/响应 schema、参数约束
- [参考生视频-wan2.7.md](./参考生视频-wan2.7.md) — `wan2.7-r2v` 请求/响应 schema、参数约束、与 wan2.6 差异
- [语音合成-TTS模型.md](./语音合成-TTS模型.md) — Qwen3-TTS / CosyVoice 全系列：模型选型、定价、48 音色、声音复刻/设计 API、Instruct 指令控制
- [阿里百炼费用参考.md](./阿里百炼费用参考.md) — 文本/图像/视频全模型定价（CNY，已核实)

## 数据来源

- HappyHorse R2V / Wan2.7 R2V API schema:阿里云 Model Studio 官方 API 参考(2026-05 截止)
- 文本/图像/视频定价:百炼控制台模型市场页面截图,已逐项核对
- 未在本目录覆盖的字段(如 i2v 首尾帧、t2v 文生视频)按官方文档惯例与 R2V schema 同构,实现时按需扩充

## 维护约定

- 这里的 schema/定价是 **真值快照**,不是猜测,可直接用于 backend 实现与 ModelInfo 注册
- 阿里官方文档可能改版,字段名/路径/价格如发生变动,以官方为准并回写本目录
- 实现层不应再"为了 robust 而打补丁"——若与本目录冲突,先确认阿里官方,再决定改代码还是改文档
