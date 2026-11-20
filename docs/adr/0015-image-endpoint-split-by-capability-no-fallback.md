---
status: accepted
---

# 图像调用端点区分文生图与图生图

部分中转站只实现图片生成或只实现图片编辑。OpenAI 兼容图片提供通用、仅文生图与仅图生图三种端点，供应商模型可挂接符合其协议约束的一种。请求按参考图确定所需能力，引用无法读取或能力不匹配时在调用前报错。这样用户可以处理缺失输入，避免提交了与预期不同的图像请求。

配置中的任务类型桶和默认模型见 [ADR 0054](0054-media-model-capability-buckets.md)，执行时的定桶见 [ADR 0001](0001-image-capability-resolved-at-execution.md)，两侧能力求值见 [ADR 0104](0104-capability-sources-split-by-dimension.md)。
