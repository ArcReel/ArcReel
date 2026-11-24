---
status: accepted
---

# 内置供应商的多字段凭证使用定型列

可灵的双密钥等内置供应商凭证按注册表字段名存为独立列。配置装载、表单和通道构造共用字段名，每个密钥独立脱敏。凭证仍属于 [ADR 0016](0016-provider-multi-credential-manual-active.md) 中可手动切换的同一实体。

为少量已知字段使用可空列会产生稀疏数据，代价低于引入动态字段的逐键校验、脱敏和关联查询。自定义供应商只保留单密钥连接模型，见 [ADR 0008](0008-custom-provider-credentials-single-field.md)。
