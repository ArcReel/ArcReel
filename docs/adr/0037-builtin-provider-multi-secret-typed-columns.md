---
status: accepted
---

# 内置 provider 的多 secret 凭证用按 registry key 命名的定型列存储，不用 JSON / 子表 / 拼接

可灵 Kling 是首个需要**两个 secret 字符串**（`access_key` + `secret_key`，JWT HS256 鉴权）的内置 provider。`docs/adr/0008` 已定「多字段凭证只走内置 provider 的 `PROVIDER_REGISTRY.required_keys: list[str]`」，但那只是**声明层**——内置 provider 凭证的**存储**在 `provider_credential` 表（`docs/adr/0016` 的多凭证 + 手动活跃机制），该表是固定列 `api_key` / `credentials_path` / `base_url`，`overlay_config()` 也只搬这三列，放不下第二个 secret。既有的「多字段」provider 其实都不是两个 secret 字符串：gemini-vertex 是单个 service account 文件路径（`credentials_path` 列），其余均为单 `api_key`。

我们决定**给 `provider_credential` 表新增按 registry key 命名的定型列**（`access_key`、`secret_key`，nullable Text），`overlay_config()` 按列名原样产出 config（`config["access_key"] = ...`），使 **registry key 名 = 表单字段名 = backend 构造参数名 = config dict key** 四者全程同名、不引列名↔key 的翻译层；脱敏逐列复用 `mask_secret`。**明确不采用**：① 把 `access_key:secret_key` 拼进单个 `api_key` 再拆（`docs/adr/0008` 已否决，破坏 `mask_secret` 单密钥展示语义）；② 通用 JSON `extra` 列（`docs/adr/0016` 已嫌「单 JSON 字段并发读改写复杂」、且要补逐字段脱敏管线）；③ 凭证字段子表（为一个 provider 引入一张表 + join，机械量最大）；④ 复用 KV `ProviderConfig` 存可灵 secret（绕过 `docs/adr/0016`：不参与多凭证活跃切换、ready 判定看不见，要给 resolver 开特例）。

这是 YAGNI 取舍：当前只有可灵一家需要两个 secret 字符串（火山 visual CV 的 AKSK 不是本批次内置 provider、Vertex 是单文件路径）。为一家上 JSON/子表机制不划算；定型列也延续了表里 `credentials_path` 这种「provider 专用定型列」的既有先例。

## Consequences

- **列膨胀风险显式接受**：`provider_credential` 表上 `access_key`/`secret_key` 对除可灵外的所有 provider 恒为 NULL（稀疏列）；`api_key` 与 `access_key` 近义列并存。靠本 ADR 与 registry 声明解释，不视作待清理的死字段。
- **重构触发点**：当出现**第三个**需要 ≥2 secret 字段的内置 provider（尤其字段名各异时），应重新评估升级到通用 JSON `extra` 列或凭证字段子表——届时是有依据的重构，本 ADR 转 superseded。
- **守住既有边界**：上承 `docs/adr/0008`（不拼字符串绕过、多字段只走内置 provider）、`docs/adr/0016`（凭证走专表 + 定型列，不用 JSON）。任何后续 PR 想改用 JSON/子表/拼接须先 deprecate 本 ADR。
- **改动收敛在凭证层**：模型 + 一条 additive nullable 加列迁移（无 backfill，两列不进任何 `WHERE`）、`CredentialRepository.create/update`、`overlay_config`、凭证 CRUD 路由（请求体 + 逐字段脱敏响应 + 字段元数据）、设置页凭证表单（按 `required_keys` 渲染）。provider 专有的连通测试（如可灵 JWT 签名探活）不在本层，随各 provider 接入。
