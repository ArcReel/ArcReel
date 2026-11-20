---
status: accepted
---

# 生成任务从一次配置解析取得实际调用通道和请求事实

如果生成任务在构造通道后再次查询配置，两次读取之间就可能产生「按配置 A 发起调用、却按配置 B 校验能力」的竞态。任务通过 [`resolve_generation_context`](../../server/services/tasks/generation_context.py) 声明所需媒体通道，一次解析并构造，只把需要的通道结果交给消费方。图片任务因此也可在没有视频配置的项目中执行。

能力和请求参数按实际构造出来的调用通道求值。规范供应商 ID 来自解析身份，模型取通道实际模型；通道内部的后端族名可能由多个供应商共用，不能代替供应商 ID。需要的通道构造失败时结束该次任务，避免静默改用另一模型产生费用。缓存职责留在调用方，构造见 [ADR 0039](0039-builtin-backend-construction-declarative-seam.md)。

视频的分辨率省略、时长约束与预检行为统一由 [ADR 0086](0086-video-request-facts-evaluated-once.md) 决定。
