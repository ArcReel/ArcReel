# CreateAsset 本地文件上传链路调查

> 日期：2026-11-01。背景：#2249。本文调研仅接受 URL 的 CreateAsset 接口如何接收本地素材，并区分素材的长期存储与入库前的临时上传中转。调研范围限定为四个开源项目的固定版本源码；过程中未配置凭证，也未执行实际上传、CreateAsset 调用或删除实测。

## 结论

四个项目的素材入库路径均为「本地 bytes → 可被供应商读取的 HTTP(S) URL → CreateAsset」，均未发现直接将文件 bytes、multipart 或 Base64 交给 CreateAsset 的实现。它们的差异在于中转位置：用户自行配置的 TOS/S3、Comfy.org 托管存储，或 OSS/tmpfiles.org。具体调用链见下表及各节源码链接。

火山官方 [CreateAsset 文档](https://docs.volcengine.com/docs/ark/create-asset-api?lang=zh)要求 `URL` 为可公网访问的媒体下载链接，不支持 Base64；接口异步处理。因此「请求返回素材 ID」与「供应商已完成下载和处理」应分开考虑。临时 URL 必须覆盖异步读取窗口；下面的 6 小时、12 小时均为项目实现值，不能当作供应商 SLA。

| 项目与固定版本 | 本地文件如何进入素材库 | bytes 的接收方 | 结论 |
|---|---|---|---|
| `byteplus-sa/ark-mcp`，`4ae9fbc31c98246607f83d32d4381b6e27305666` | `media_upload` 上传至 TOS/S3；`ark_asset_create` 将 object key 签发为 GET URL 后入库 | 用户配置的 TOS/S3 bucket | 对象存储中转 |
| `byteplus-sa/ComfyUI-BytePlus-ModelArk`，`d3a91b9c8bb314ea1a1327215378d62a72d6a20d` | ComfyUI 图片/视频/音频 → Comfy.org 上传 helper → URL → CreateAsset | Comfy.org 上传服务 | 第三方托管中转 |
| `IanLin419/byteplus-ai-gen-platform`，`9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2` | 服务端签发 PUT URL；浏览器直传 TOS；再签发 GET URL 并调用 CreateAsset | 用户配置的 TOS bucket | 浏览器直传对象存储，而非直传 CreateAsset |
| `magicwang1111/ComfyUI-Seedance`，`896728c266a6831286641c2b436d563aeaf8982a` | 图片转为临时 PNG；配置齐全时走 OSS，否则走 tmpfiles.org；生成 URL 后调用 CreateAsset | OSS 或 tmpfiles.org | 对象存储/第三方托管中转 |

## 1. ark-mcp：上传与供应商入库分开

[`media_upload.py` 的 `prepare_upload`、`upload_one`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/media_upload.py#L126-L202) 会区分本地路径与解码后的 Base64 bytes，分别调用 `gateway.upload_file` / `gateway.upload_bytes`，再调用 `presign_get` 返回 URL、object key 与 URL 到期时间。这里接收 Base64 的是 MCP 工具本身，而非 CreateAsset。

[`make_object_storage_gateway`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/providers/object_storage.py#L69-L91) 按配置选择 S3 或 TOS，并要求提供相应的 access key、secret key 和 bucket；[`TosGateway`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/providers/tos/client.py#L60-L107) 负责执行对象上传及 GET 预签名。仓库的 [S3 存储说明](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/docs/s3-object-storage.md)明确采用私有 bucket 并通过预签名 URL 临时授权读取；这意味着部署者必须自行提供对象存储配置，并非由项目作者免费托管。

[`ark_asset_create`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/ark_asset_create.py#L29-L100) 同时接受已有 URL 和 object key；若传入 object key，会以 `_SOURCE_URL_TTL_SECONDS = 6 * 3600` 重新签名，再调用 `service.create_asset(url=...)`。上传工具默认的签名有效期与素材入库所用的签名有效期不能混为一谈。所读的上传、签名和入库函数均未体现对上传对象的自动删除；URL 到期不能作为对象已被删除的证据。

## 2. ComfyUI-BytePlus-ModelArk：借用 Comfy.org 上传能力

[`upload_image_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L204-L221) 调用 ComfyUI 内置的 `upload_image_to_comfyapi`；源码明确要求登录 Comfy.org，且该 helper 随 API nodes 一并提供。视频路径使用 [`upload_video_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_video.py#L108-L138)；音频则通过 [`upload_bytes_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L747-L790) 将 `BytesIO` 交给 `upload_file_to_comfyapi`。

[`register_core_asset`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L715-L765)先 `source_url = await upload()`，随后调用 `create_asset`；[`create_asset`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L334-L358)发送带 `URL` 的 JSON。也可直接传现成公开 URL 跳过 Comfy.org 上传。这不是 BytePlus 原生文件入库接口。

源码将链接复用缓存设为 12 小时，并在注释中称 Comfy.org 链接约在 24 小时后删除；这里只能确认项目是按此假设编写的，并未核验 Comfy.org 当前的保留政策或获得任何服务保证，不能把代码注释当作托管 SLA。[缓存实现](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L747-L790)

需要注意避免同仓库内的命名误导：[`upload_file_to_ark`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L415-L470) 确实调用了 `client.ark.files.create(file=...)` 上传 bytes 并返回 file ID，但上述素材入库路径并未使用它作为 CreateAsset 的输入。Files API 支持上传文件，不能据此推导 CreateAsset 也支持接收 bytes 或该 file ID。

## 3. byteplus-ai-gen-platform：签名 PUT/GET 与素材入库串联

[`uploadToTos`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/api/tos.ts#L102-L125) 先向服务端请求 PUT URL，由浏览器执行 `fetch(url, {method: 'PUT', body: file})` 上传文件，再请求 GET URL。文件 bytes 直接写入 TOS，应用服务端仅负责签名。

[`uploadOneAsset`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/hooks/useAssetUpload.ts#L125-L185) 在上传完成后，会额外将 GET URL 重签为 12 小时有效期，再把 `longGet.url` 传给 `createAsset`，随后轮询处理状态。[TTL 常量](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/hooks/useAssetUpload.ts#L9-L16)与[签名器](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/server/signers/tos.ts#L1-L8)分别设定了素材 GET 12 小时、PUT 15 分钟的有效期，这些都属于代码层面的选择。

配置包括 AK/SK、region、endpoint、bucket、key prefix 和默认 GET TTL；[`createTosRouter`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/server/routes/tos.ts#L39-L88)按请求中的 `creds` 创建 TOS client，因此存储账户由用户配置。注意 [`src/api/tos.ts` 实际代码](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/api/tos.ts#L34-L55)从前端 store 取 AK/SK 并随签名请求发送；文件开头「浏览器看不到 AK/SK」的旧注释与此不符，不能照抄成安全结论。所读上传/入库链路未展示 TOS 对象自动删除，签名过期也不代表对象清理。

## 4. ComfyUI-Seedance：OSS 与 tmpfiles.org 两种中转

[`_upload_image_for_asset` 及入库节点](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/nodes.py#L938-L952)先将图片写为临时 PNG 并调用 `upload_file_to_public_url`；[入库调用](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/nodes.py#L1368-L1381)再把得到的 `source_url` 交给 client。

[`py/api/upload.py`](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/api/upload.py) 在 OSS 配置齐全时上传至 OSS，否则调用 tmpfiles.org 的 multipart 上传接口并转换出下载 URL。OSS 路径先通过 `PUT` 上传文件内容，再返回默认 86400 秒有效的签名 GET URL；而 tmpfiles.org 属于额外的第三方中转服务，并非供应商素材库。该 helper 未体现对 OSS 对象的删除，节点 `finally` 块中的清理仅针对本地临时文件，并不代表远端文件已删除。

[`py/api/asset.py` 的 `create_asset`](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/api/asset.py) 会校验 HTTP(S) URL，并以 `URL` 字段请求 CreateAsset。本次调研未核验 tmpfiles.org 当前的保留期限、配额或服务条款，不据此对其生命周期作任何承诺。

## 对 ArcReel 的可选最小方案

以下是调研阶段对比的方案。ArcReel 的交付范围见 [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559)，独立建模的理由见 [ADR 0108](../adr/0108-material-storage-independent-of-provider-registration.md)，中转实现见 [relay.py](../../lib/object_storage/relay.py)。

| 选项 | 最小链路 | 需要作出的决定 |
|---|---|---|
| 只接收已有公开 URL | 用户提供供应商可读取的 URL → CreateAsset → 记录供应商关联 | 本地素材暂不支持自动入库，或由用户自行提供中转链接 |
| 可选的用户自有 TOS 临时中转 | 本地原件保持现有存储；入库时上传副本 → 签 GET URL → CreateAsset → 跟踪终态 → 按明确策略清理副本 | 是否接受凭证配置、上传流量/费用、失败残留及清理责任 |
| 复用已有公网媒体地址 | 现有部署能提供足够有效期的可下载 URL → CreateAsset | 实际公网可达性、下载鉴权方式及有效期是否满足供应商异步拉取 |

第二项可被限制为供应商入库过程中的传输步骤，不必把 ArcReel 素材主存储迁到 TOS，也不必交付完整长期对象存储后端；这一边界可参考 ark-mcp 的上传与入库分离实现。它仍然需要上传、有效期和清理机制，不能因叫「临时」而省略。[对应实现](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/ark_asset_create.py#L29-L100)

若选择临时中转，建议最小范围为一种用户可控后端、显式配置与启用、记录临时对象标识、入库终态后清理及失败重试策略；不要照搬第三方匿名托管的静默兜底。清理时点需要真实验证供应商异步读取与失败重试行为，不能仅在 CreateAsset 返回 ID 后立刻删除，也不能把 URL 过期视为删除。源码足以证明中转模式存在，尚不足以给 ArcReel 的最短 URL 有效期、删除安全时点或生产可靠性作保证。

## 证据限制

- 固定版本源码证明实现意图与调用参数；未运行这些项目，未验证账号区域、权限、费用、供应商兼容性或实际请求成功率。
- BytePlus 开源项目可作为上传模式参考，不能直接证明火山国内站的鉴权、endpoint 和素材权限完全相同；火山 CreateAsset 的输入约束以官方文档为准。
- 本文只核查所列上传、签名与入库路径。未对整个仓库做全量清理机制审计，因此「未展示自动删除」不等于证明所有部署均不清理。
