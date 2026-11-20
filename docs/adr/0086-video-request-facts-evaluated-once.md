---
status: accepted
---

# 视频请求事实在每次求值中共用同一组输入和结果

过去规划、预检、报价与执行各自推导分辨率、时长和模型，曾导致用户确认的档位与实际提交不一致。现在统一通过「视频请求事实」完成求值：读侧采用当前配置解析出的身份，执行侧采用实际调用通道的身份，其余规则完全一致。排队期间的编辑会在执行时重新投影，已提交任务的恢复则沿用执行快照。见 [ADR 0049](0049-generation-context-single-resolution.md) 和 [ADR 0054](0054-media-model-capability-buckets.md)。

- 输入按实际可用参考图决定任务类型桶。参考路线中，没有参考图的单元使用图生视频桶。该桶不可用时显示档位未知与原因，避免用另一个模型的档位放行。
- 两条视频路线都按 [ADR 0019](0019-resolution-none-means-omit-param.md) 保留未设置的分辨率。时长与报价读取实际下发的请求值，见 [ADR 0056](0056-execution-predicates-same-source-as-request.md)。
- 时长用模型条目的离散档位及生效覆盖，按实际分辨率、参考图与尾帧取交集。时长声明缺失、收窄后为空或选择越界，均在付费提交前报错。ComfyUI 的端点固定时长是合法空集，见 [ADR 0082](0082-node-bindings-are-the-only-truth-of-comfyui-endpoint.md)。Vidu 的端点级时长校正处理同一型号在不同 API 端点上的合法值差异，见 [ADR 0104](0104-capability-sources-split-by-dimension.md)。
