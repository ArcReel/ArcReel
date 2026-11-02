---
status: accepted
---

# 资产图默认端点按资产类别细分：图片解析链新增资产槽位层（全局 + 项目两层）

资产图任务（角色 / 场景 / 道具 / 商品本体与角色衍生）与分镜图、图片编辑共用同一图片端点解析链：payload > 项目桶 > 项目默认 > 全局桶（t2i / i2i）> 全局默认 > 自动推断（`docs/adr/0054`）。用户想让不同资产类别走不同生图模型（如角色用高细节模型、道具用低成本模型）时没有表达手段——批量生成里用户也没有逐次切换配置的机会，只能靠逐任务 payload 或改配置再补生成。

决定：**在图片端点解析链新增一层按任务资产类别细分的资产槽位，且同时开放全局层与项目层。**

- 5 个资产槽位键（与既有键同为 `provider/model` 形态，空值 = 未配置回退下一层）：`default_image_backend_character` / `default_image_backend_character_derivative` / `default_image_backend_scene` / `default_image_backend_prop` / `default_image_backend_product`。键位登记在 `lib/config/resolver.IMAGE_ASSET_SLOT_KEYS`，以资产图任务的 `task_type` 为键：本体任务的 task_type 即资产类型本身，衍生任务恒为 `character_derivative`（`lib.project.asset_derivatives.DERIVATIVE_TASK_TYPE`）。
- **项目层与全局层同名同槽**：项目槽位取 project.json 的同名键（经项目写边界 `validate_backend_value` 校验），全局槽位取设置键。两侧用同一把 `IMAGE_ASSET_SLOT_KEYS` 键位表，取值来源不同。
- 解析顺序：payload > **项目资产槽位** > 项目桶 > 项目默认 > **全局资产槽位** > 全局桶 > 全局默认 > 自动推断。资产槽位在所属层内最具体：项目槽位比项目桶与项目默认更具体、排在项目桶之前；全局槽位比全局桶更具体、仍排在项目层之后。未登记的 task_type（分镜图、图片编辑等）两层都跳过，解析结果与原先逐字节一致。
- 执行路径沿用既有 task_type 透传：入队时执行身份派生与 worker 认领限流路由都带 task_type 解析（图片任务不锁定执行身份，docs/adr/0001，执行时重解析为准）；`resolve_generation_context` 带 `task_type`，资产图与角色衍生的任务执行器传自身 task_type；报价（资产图批量预览）按（生成路径，资产类别）组合解析执行身份，与执行同口径。项目槽位随 project dict 一并传入解析，无需额外透传。
- 能力闸不变：槽位与默认层同口径，候选不过滤（默认层不承诺能力），执行时按实际生成路径（t2i / i2i）校验，能力不满足显式报错不静默换模型。
- 自定义供应商 / 模型删除时，槽位键与其他默认 / 桶键同入级联清理范围（全局设置侧）；三语标签沿用 `global_bucket_label_<key>` 约定，全局与项目设置页复用同一批文案（受 `test_global_bucket_keys_have_i18n_labels` 守护）。

## 明确不采用

- **每类别再分 t2i / i2i 双槽**：本体任务的生成路径执行时才定（是否声明参考图），再细分配置面爆炸；能力缺口已有解析闸兜底。
- **资产槽位排在桶 / 默认之后（弱优先级）**：用户已配全局 i2i 桶或项目默认时会覆盖类别设置，「设了类别却跑别的」比「类别覆盖桶 / 默认」更难理解；槽位在所属层内取最高优先级。
- **按资产槽位过滤候选下拉**：与默认层一致——默认层不过滤，槽位是默认层的细化而非能力承诺。
- **创建向导暴露资产槽位**：创建向导只呈现默认层（与 t2i / i2i 桶一致），资产槽位是项目级细分，留给项目设置页。

## Consequences

- 解析骨架 `_resolve_layered_backend` 增加两个可选资产槽位层参数（`project_asset_slot_key` / `asset_slot_key`，键位参数化、不硬编码进媒体键位表）；视频 / 文本骨架不传槽位键位、不受影响。
- `resolve_image_backend` 全部调用点需审计：只有资产图任务的调用方（执行器、入队派生、worker 路由、批量报价）传 task_type；其余图片任务不传，行为不变。
- 项目写路径（`CreateProjectRequest` / `UpdateProjectRequest` / `_PROJECT_BACKEND_FIELDS`）新增 5 个槽位字段，与桶 / 默认字段同走通用校验与落盘；project.json 为明文文件、无字段白名单，新增可选键无需 schema 迁移。
- 设置 UI：全局设置页在图片通道能力细分区下新增「资产图分类默认模型」区；项目设置页在同一图片通道内新增同区，留空显示穿透演算后的生效模型（项目桶 → 项目默认 → 全局同类槽位 → 全局桶 → 全局默认）。
- 存量行为不变：无任何槽位有值时，解析链退化为 docs/adr/0054 现状。
