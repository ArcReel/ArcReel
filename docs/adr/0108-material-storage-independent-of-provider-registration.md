---
status: accepted
---

# 素材存储与供应商素材关联独立建模

创作流程引用素材，存储层负责保存实际内容，供应商素材关联则将某个内容版本映射到特定供应商范围内的素材。若把 `local / s3 / volcano` 混为互斥类型，会把存储迁移、供应商切换与内容替换强耦合在一起，也无法表达「同一内容版本存放在对象存储中，同时挂接多个供应商关联」的场景。

仅迁移存储不改变内容身份或产物时效。改选 B 不会让它继承 A 的关联，A 的关联仍然属于 A。解除本地关联也不删除供应商资源。ArcReel 需要保存可取得的真实内容，以便预览、编辑、归档和改用其他供应商。仅有供应商素材 ID 不能充当可用内容。版本事实见 [ADR 0109](0109-content-version-identity-on-version-records.md)，供应商素材导入见 [ADR 0112](0112-import-provider-asset-saves-content-before-link.md)。

[Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559) 记录交付范围和验收。[上传链路研究](../research/create-asset-local-upload-research.md)和[对象存储范围研究](../research/material-object-storage-scope.md)提供当时的证据。
