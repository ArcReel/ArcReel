---
status: accepted
---

# 供应商能力声明收敛到模型级，provider 能力为派生只读视图

供应商不同模型能力不同（如 Ark lite 文本模型不支持 structured_output，却被顶层声明覆盖会导致原生调用报错），把 `media_types` / `capabilities` 声明在 provider 顶层，必然导致声明与个别模型的真实能力漂移。决定把能力（text/image/video 各自的 capability 枚举）声明在 `ModelInfo`（模型级），`ProviderMeta` 的 `media_types` / `capabilities` 改为从其下所有模型聚合的只读 `@property`、不独立存储——能力的真相源是模型，provider 级能力是 derived view、不可写入。

## Consequences

- 能力查询都要从 models 聚合，换来单一真相源；provider 解析、自动推断、前端模型选择器、计费派发都依赖这个数据形状。
- registry 的能力声明是**描述性元数据**，backend 运行时构造出的能力集才决定实际发往上游 SDK 的参数，二者可有意不一致（与 `docs/adr/0001`「声明 vs 执行」分层一脉相承）。
- 新增模型 = 在其 `ModelInfo` 写一条能力声明即可，不必改 provider 顶层。
