---
status: accepted
---

# 执行期判据与供应商请求同源

配置会经过默认值、归一化和协议映射，按中间字段判断供应商行为会与真正发出的请求分叉。校验、音轨和计费维度应读真实的请求字段，或直接复用请求构造使用的变换函数。例如需要按分辨率求时长和报价时，读实际下发的分辨率。

计费维度允许基于供应商规则单独派生。例如某个通道恒按含音计费时，计费中的音频标志无法代表请求开关或成片是否有声。相应事实各自保留，由消费层按用途读取。关联规则见 [ADR 0086](0086-video-request-facts-evaluated-once.md)、[ADR 0100](0100-price-table-meters-and-dimensions.md) 和 [ADR 0104](0104-capability-sources-split-by-dimension.md)。
