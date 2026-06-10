---
status: accepted
---

# 视频时长以 per-model supported_durations 为单一真相源，原值透传、解析不到即 fail loud

backend 内的桶映射（把 6 静默改成 8）和 `or [4,6,8]` 隐性 fallback 是「选 6s 却被改成 8s、再被对端拒为非法」事故的根因。决定每个视频模型声明一个非空离散 `supported_durations`，三个消费点（剧本 prompt / 前端选择器 / 视频请求体）同源消费；各 backend 删除 duration 桶映射与归一化、请求体原值透传、越界由对端以 400 反馈；resolver 拿到空集时抛 `ValueError`、删除所有隐性 fallback——宁可 fail loud 引导用户在配置页修正，也不静默篡改用户/LLM 的选择或掩盖配置缺陷。

## Consequences

- schema 层不引入连续区间类型，改用 list 全展开 + 前端检测连续性的折中。
- 自定义供应商缺省时由 model_id 启发式预设表预填（未命中回退保守默认），Alembic 回填迁移内联复制预设快照而非 import 模块，以保历史迁移确定性。
- 一处受限例外：Vidu 因 API 按 endpoint 列出差异很大的合法时长集，保留 `_coerce_duration` 端点级就近校正 + warning，与 model 级单一真相源是不同维度。
