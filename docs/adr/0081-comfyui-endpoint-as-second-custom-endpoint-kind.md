---
status: accepted
---

# ComfyUI 端点：一份 workflow 作为自定义调用端点的第二种 `kind`

用户自建的原生 ComfyUI（本机、内网或自有云主机）要作为生图与生视频的供应商通道接入 ArcReel。它与既有两类接入形态都不合：内置供应商的模型列表是随版静态字典，装不下运行时导入的 workflow；声明式端点的流派边界是「JSON in/out + 提交/轮询」，而 ComfyUI 的请求体是一整张节点图、素材走 multipart 上传、产物是二进制文件，正是 `docs/adr/0067` 明确不为之扩格式的构造。

我们决定**一份 ComfyUI workflow 连同其节点绑定就是一个自定义调用端点（ComfyUI 端点，`kind: comfyui`），一台 ComfyUI 服务就是一个普通自定义供应商（`discovery_format: comfyui`），供应商的模型行挂接 ComfyUI 端点**。workflow 以 API 格式原样内嵌在端点定义里，定义走 `docs/adr/0067` 为 `kind` 预留的槽位，整份存 `custom_endpoint.definition`，镜像列照旧由定义派生；端点 CRUD、导入导出、被引用拒删、端点测试框架与市场载荷全部复用。运行时按 `kind` 分叉：`comfyui` 产出 Python 实现的图像 / 视频 backend，不经声明式运行时。媒体类型由端点定义决定，一个 ComfyUI 供应商可同时挂图像与视频端点。

**明确不采用**：① **新增内置供应商**——workflow 是用户运行时导入的，内置模型列表装不下。② **扩充声明式格式**以表达节点图、multipart 与二进制产物——这会击穿 0067 的流派边界，且表达出来也没有通用性。③ **另起一族实体**（workflow 表 + workflow 模型行）——复制一遍端点已有的 CRUD、导入导出、拒删与市场载荷，而 `kind` 槽位本就是为第二种实现形态留的。④ **一份 workflow = 一个模型行**——模型行不持有定义本体，无法导入导出与进市场，也失去「一份 workflow 挂到多台机器」的能力。

## 凭据头模板放在端点定义里，供应商行不加列

ComfyUI 本体零鉴权，用户加的反向代理鉴权方式各异（header 或 query）。凭据头模板放在端点定义的 `auth` 节，与声明式端点同一 schema，`{{api_key}}` 只许出现在此节，`api_key` 留空时整节不渲染。供应商行不为此加列，`docs/adr/0008` 的单字段凭证模型原样守住。已知代价有两处：鉴权方式按部署而异，同一 workflow 挂到两台鉴权不同的机器需复制一份定义；供应商级的连通性检查拿不到端点级凭据模板，只能与 openai 协议探针同款、以 `api_key` 作 Bearer 裸打 `/system_stats`（`api_key` 为空则不带凭证），用自定义头做反向代理鉴权的部署下探针可能报不可达，此时以测试连接为准。两者都接受为边缘场景。

## 端点与供应商协议首次双向绑定

此前任何自定义调用端点可挂到任何自定义供应商。ComfyUI 端点不能：服务端双向校验，`kind: comfyui` 的端点只能挂在 `discovery_format: comfyui` 的供应商上，该协议供应商的模型行也只能挂 ComfyUI 端点，选择器按协议过滤。约束只在 comfyui 协议上生效，其他协议的自由挂接不变。原因是 ComfyUI 端点的运行时（`/upload/image`、`/prompt`、`/history`、`/view`）只对 ComfyUI 服务有意义，声明式端点对 ComfyUI 服务同样无意义，错挂只会在生成时才爆。

## 没有供应商账单，参考费用沿用单价字段，执行时长只作记录

ComfyUI 是用户自己的显卡，没有供应商账单。不发明 GPU 计价：参考费用沿用模型行现有单价字段与现有公式（视频按片长、图像按张），单价为空即 0；执行时长照常记录，供企业分摊场景参考，但不进计价。记账层零改动。

## 取消与超时叫停远端，在 backend 内部完成而不扩协议

ArcReel 放弃一次生成时（用户取消或轮询超时，生成任务与测试连接同）best-effort 通知 ComfyUI 取消该任务，释放显卡；这是全仓库唯一通知供应商的取消——商业 API 的任务放弃后只是不再轮询，供应商侧照跑照计费，通知没有意义，而 ComfyUI 侧不叫停就白占用户显卡。实现不扩调用通道协议：叫停远端发生在 ComfyUI backend 自己的生成流程内部，本地放弃后原有的失败路径照走；远端叫停失败只记日志，本地状态机（`docs/adr/0006`）不受影响。代价是显卡慢的用户可能在超时上限丢掉快跑完的结果，由可配置的轮询超时缓解。

## Consequences

- 端点定义 schema 按 `kind` 分两种，不同构：声明式定义有请求模板与 `capabilities` 节，ComfyUI 定义有 `workflow` 与 `bindings` 节（见 `docs/adr/0082`）。共享校验器按 `kind` 分派。
- `discovery_format` 新增 `comfyui`：连通性检查打 `/system_stats` 并回传 `comfyui_version`，模型发现返回结构化的「不适用」而非空列表；该协议供应商并发默认 1。
- 「自定义调用端点恒为视频」的假设退役，媒体类型一律读端点定义。
- 端点测试对 ComfyUI 端点只有预览请求与测试连接；验证响应不提供——产物提取是固定代码，无用户可配的取值路径可验。
- 首期只轮询 `/history`，不接 WebSocket；不显示 ComfyUI 侧排队位次，不报假进度。
- ComfyUI 端点定义天然是市场条目载荷，市场条目类型不变；市场首期是否展示与安装 ComfyUI 端点不在本 ADR 范围。托管型 ComfyUI 云服务（Comfy Cloud、RunningHub 一类）也不在范围，它们走声明式端点。
