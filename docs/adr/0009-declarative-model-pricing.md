---
status: proposed
---

# 计费定价改为代码级声明式：定价并进 `ModelInfo`、按 `kind` 派发，不引入运行时 DB+UI 改价

ArcReel 的 `CostCalculator`（`lib/cost_calculator.py`）当前把每个供应商各模态的费率写成**散落的类属性 dict**（`IMAGE_COST` / `VIDEO_COST` / `ARK_VIDEO_COST` / `GROK_TEXT_COST` / `OPENAI_IMAGE_TOKEN_COST` 等十余张），`calculate_cost` 用一长串 `if provider == X` 手工路由到对应的 per-shape 计算函数，币种 USD/CNY 混在各函数里。新增一个内置供应商时，若不为它加显式分支，视频会**静默回落到 Veo 费率表**按错误单价计费。同期的三家供应商接入调研（`docs/research/arcreel-vendor-integration-research.md` §7 Caveats 1）提出"价格随促销波动，费率字段应配置化，不写死"。

评估时确认了一个关键事实：**ArcReel 的成本是快照的**（见 `CONTEXT.md`「成本快照」）。`finish_call` 仅在调用完成那一刻调一次 `calculate_cost`，把结果冻结进 `ApiCall.cost_amount`；所有用量/费用聚合读冻结值，不重算。因此"为历史计费保留下线模型费率"这一诉求并不成立——过往记录不依赖费率表。我们也对照了 LiteLLM 的做法：它用单一数据表按 model 名建索引，每个 model 条目内同时承载元数据、上下文窗口与定价（`input_cost_per_token` / 每图 / 每秒等），按 `mode` 派发计算，定价与模型元数据**不分家**。

我们决定把计费改为**代码级声明式**，而**不**做运行时 DB+UI 改价。具体：① 定价数据作为 `ModelInfo.pricing` 字段**并进 `PROVIDER_REGISTRY` 的每个模型条目**（每模型单一真相源，与 LiteLLM 对齐）；② `Pricing` 的类型定义与按 `kind` 标签（`per_token` / `per_image_flat` / `per_image_by_resolution` / `per_image_openai_token` / `per_second_matrix` / `per_token_video` / …）选择的计算**策略放独立 `lib/pricing` 模块**，把"计算关注点"从模型声明里分离；③ `calculate_cost` 改成**按 `pricing.kind` 派发**而非 `if provider == X`；④ 模型下线用 `ModelInfo.hidden` 标记（从 UI 下拉剔除、保留条目供"入队后、finish 前被下线"这一边角仍能算价），不建独立的历史费率仓库；⑤ **全量迁移**现存全部内置供应商（Gemini/Ark/Grok/OpenAI/Vidu）到新系统，纯重构、行为零变化，以全量 pytest 对拍旧值兜底。运行时改价（促销时不重部署即可改）如真有需要，是未来另一个独立 epic，本 ADR 不预设。

## Consequences

- **`calculate_cost` 变薄、可扩展**：`if provider == X` 路由链被 `kind` 派发取代。新增内置模型 = 在其 `ModelInfo.pricing` 写一条声明 + 复用已有 `kind` 策略，**不再动 `calculate_cost` 逻辑、不再加 provider 分支**，从根上消除"新供应商视频被静默按 Veo 费率算"的回归类型。
- **改促销价 = 改声明 + 重部署**：这是 conscious trade-off。代价是不支持非技术人员在运行时改价；收益是无需 DB schema/迁移/仓库/设置页 UI/三语 i18n/校验这一整套 surface。ArcReel 自托管、重部署是常态，且成本仅用于费用预估与内部记账（非对外计费），精度诉求是"不离谱"而非"实时可调"。
- **历史费率无需入库**：因成本快照，过往 `cost_amount` 已冻结，定价数据只需覆盖**当前可选**模型。下线模型不进入定价数据；`ModelInfo.hidden` 兜住"入队后被下线、finish 时仍需算价"的罕见边角，无须维护历史费率 graveyard。
- **自定义 provider 价格不并入 `ModelInfo`**：自定义供应商的单价是用户在 DB（`CustomProviderModel.price_input/output/currency`）填的、非静态，`calculate_cost` 保留 `is_custom_provider` 早分支走参数化路径（调用方预查 DB 传入）。这与 [ADR 0008] 的"自定义 provider 凭证/配置由 DB 承载、不进静态 registry"一脉相承。
- **定价与能力声明同地但职责分层**：数据并进 `ModelInfo`（单一真相源、改 promo 一处可见），但 `Pricing` 的**类型与计算策略**在 `lib/pricing` 独立模块——数据声明式、逻辑可单测，二者不耦合在 `cost_calculator` 一个文件里。
- **`ModelInfo` 增量扩展，不重设计**：本 ADR 只给 `ModelInfo` 加 `pricing` 与 `hidden` 两个字段，**不**触动现有 `capabilities: list[str]` 与 `VideoCapability` 枚举重复这类既有小瑕疵（留作独立议题，避免范围蔓延）。
- **阶梯/缓存等高级计费形状按需增 `kind`**：首批 `kind` 集合覆盖现存全部计费形状以保证零行为变化；Qwen 的输入量阶梯价首批按 0-128K 基础档单价（flat）计，阶梯建模作为后续 `per_token_tiered` kind 的扩展点，不在本次纯重构内引入。
- **与 ADR 0008 互补**：0008 划"凭证模型不被协议形态污染"的边界，0009 划"计费数据不被散落硬编码污染、定价形态由 `kind` 而非 provider 决定"的边界；两者都把"运行时确定 vs 启动时声明"的分界讲清楚。任何后续 PR 想引入运行时 DB+UI 改价、或把定价搬回散落 dict，须先 deprecate 本 ADR。
