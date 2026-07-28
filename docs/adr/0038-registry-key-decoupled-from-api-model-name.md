---
status: accepted
---

# 两栖多模态模型用 registry 别名键 + `api_model_name` 解耦，不给 registry 键改复合 `(model_id, media_type)`

`PROVIDER_REGISTRY[provider].models` 以 model_id 字符串为唯一键，一个 model_id 对应一个 `ModelInfo`、一个 `media_type`。这个键同时兼三职：**UI / 持久化标识**（前端模型选择器、`project.json` 的 `"model"`、`ApiCall.model` 列、API 请求体里都是这个裸字符串）、**计费 / 能力查表键**（`lib/pricing/lookup.py` 与各能力查找按它精确命中）、以及**发给供应商 API 的模型名**。三职共用一个全局唯一字符串，在「两栖多模态模型」上破裂：可灵 v3-omni 的图像与视频在可灵 API **同叫 `kling-v3-omni`**，但单键单 `media_type` 容不下同一 API 名的两个模态条目。

我们决定**给 `ModelInfo` 加 `api_model_name: str | None = None`**（默认 `None` 回退键名、存量条目零影响），把「发供应商的 API 名」从「内部唯一键」这一职里剥离：图像条目用**别名键** `kling-v3-omni-image`（`api_model_name="kling-v3-omni"`）、主键 `kling-v3-omni` 归视频；图像后端发 `api_model_name or 键名`（视频后端因唯一两栖条目占主键、键名即 API 名，暂无别名需求，目前仍直接发键名），计费与能力查表仍按 registry 键名。两条目的 `display_name` 各自区分（「可灵 V3-Omni（图像）」/「可灵 v3 Omni」），合成键不泄露到 UI。

**明确不采用**：把 registry 键改成复合 `(model_id, media_type)`。复合键确实在 registry 这一层更贴合现实（一个 API 模型两种模态 = 共享 model_id 的两行，无需别名键也无需 `api_model_name`），但它动的是 model_id 本无碍的前两职：model_id 是**跨层身份 token**，前端选择 / `project.json` / `ApiCall.model` / API 请求体 / pricing 查表全是**不带 `media_type` 伴随的裸字符串**。复合键会把代价推到所有这些边界——尤其成本快照（`docs/adr/0009`）在 finish 那一刻只有模型字符串、历史 `ApiCall` 行无从补回 `media_type`，pricing 模块内部的费率 dict 也按模型字符串键、会一并被迫复合。两栖只冲突 model_id 的「API 名」一职，为它给全系统上复合键税、且全 registry 仅一个两栖模型，不成比例。原则：**只解耦冲突的那一职，不动无碍的两职。**

## Consequences

- **向后兼容**：存量模型 `api_model_name=None`、回退键名，行为零变化；新增非两栖模型无需关心此字段。
- **后端适配不对称**：目前仅图像路径消费 `api_model_name`（`KlingImageBackend` 收该参数并据以发 API 名）；视频后端尚无 `api_model_name` 参数、直接发键名。今天无碍——唯一两栖条目 v3-omni 的视频侧占主键、键名即 API 名；但若未来某两栖模型需让**视频**条目用别名键，须先为视频后端补 `api_model_name` 支持，否则会按键名误发。这是上述「重构触发点」的前置工作。
- **合成键味道显式接受**：`kling-v3-omni-image` 是供应商世界里不存在的 registry 内部标识，靠本 ADR + `display_name` 屏蔽来解释，不视作待清理的死键。
- **重构触发点**：当两栖多模态模型增多、「别名键 + `api_model_name`」样板多到让人不舒服时，重新评估复合键 `(model_id, media_type)` 或其它根治——届时是有依据的重构，本 ADR 转 superseded。
- **同族收敛**：上承 `docs/adr/0009`（定价并进 `ModelInfo`、按键查表）、`docs/adr/0013`（能力声明收敛到模型级）——三者都在回答「registry 键 / `ModelInfo` 承载什么」。本 ADR 是给 registry 键做的第三次职责剥离：把「对外 API 名」从「内部唯一键」拆出。
