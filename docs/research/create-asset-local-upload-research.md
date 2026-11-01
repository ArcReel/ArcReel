# CreateAsset 本地文件上传链路调查

> 日期：2026-11-01。背景：#2249。本文回答 URL-only 的 CreateAsset 如何接收本地素材，区分素材长期存储与入库前的临时上传中转。范围为四个开源项目的固定版本源码；未配置凭证，未执行上传、CreateAsset 或删除实测。

## 结论

四个项目的素材入库路径均为「本地 bytes → 可被供应商读取的 HTTP(S) URL → CreateAsset」。没有发现把文件 bytes、multipart 或 Base64 直接交给 CreateAsset 的实现。差别在中转位置：自己配置的 TOS/S3、Comfy.org 托管存储，或 OSS/tmpfiles.org。具体调用链见下表及各节源码链接。

火山官方 [CreateAsset 文档](https://docs.volcengine.com/docs/ark/create-asset-api?lang=zh)要求 `URL` 为可公网访问的媒体下载链接，不支持 Base64；接口异步处理。因此「请求返回素材 ID」与「供应商已完成下载和处理」应分开考虑。临时 URL 必须覆盖异步读取窗口；下面的 6 小时、12 小时均为项目实现值，不能当作供应商 SLA。

| 项目与固定版本 | 本地文件如何进入素材库 | bytes 的接收方 | 结论 |
|---|---|---|---|
| `byteplus-sa/ark-mcp`，`4ae9fbc31c98246607f83d32d4381b6e27305666` | `media_upload` 上传 TOS/S3；`ark_asset_create` 将 object key 签为 GET URL 后入库 | 配置的 TOS/S3 bucket | 对象存储中转 |
| `byteplus-sa/ComfyUI-BytePlus-ModelArk`，`d3a91b9c8bb314ea1a1327215378d62a72d6a20d` | ComfyUI 图片/视频/音频 → Comfy.org 上传 helper → URL → CreateAsset | Comfy.org 上传服务 | 第三方托管中转 |
| `IanLin419/byteplus-ai-gen-platform`，`9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2` | 服务端签 PUT；浏览器直传 TOS；签 GET；CreateAsset | 用户配置的 TOS bucket | 浏览器直传对象存储，非直传 CreateAsset |
| `magicwang1111/ComfyUI-Seedance`，`896728c266a6831286641c2b436d563aeaf8982a` | 图片转临时 PNG；配置齐全走 OSS，否则走 tmpfiles.org；URL → CreateAsset | OSS 或 tmpfiles.org | 对象存储/第三方托管中转 |

## 1. ark-mcp：上传与供应商入库分开

[`media_upload.py` 的 `prepare_upload`、`upload_one`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/media_upload.py#L126-L202)区分本地路径与解码后的 Base64 bytes，分别调用 `gateway.upload_file` / `gateway.upload_bytes`，再调用 `presign_get`，返回 URL、object key 与 URL 到期时间。这里接受 Base64 的是 MCP 工具，不是 CreateAsset。

[`make_object_storage_gateway`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/providers/object_storage.py#L69-L91)按配置选择 S3/TOS，要求相应 access key、secret key、bucket；[`TosGateway`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/providers/tos/client.py#L60-L107)执行对象上传及 GET 预签名。仓库的 [S3 存储说明](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/docs/s3-object-storage.md)明确使用私有 bucket、由预签名 URL 临时授权读取；这要求部署者提供对象存储配置，不是项目作者免费托管。

[`ark_asset_create`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/ark_asset_create.py#L29-L100)既接受已有 URL，也接受 object key；后者以 `_SOURCE_URL_TTL_SECONDS = 6 * 3600` 重新签名，再调用 `service.create_asset(url=...)`。上传工具默认签名有效期与素材入库使用的签名有效期不能混为一谈。所读上传、签名和入库函数没有展示上传对象的自动删除；URL 到期不能作为对象已删除的证据。

## 2. ComfyUI-BytePlus-ModelArk：借用 Comfy.org 上传能力

[`upload_image_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L204-L221)调用 ComfyUI 内置 `upload_image_to_comfyapi`；源码明确需要 Comfy.org 登录，并且 helper 随 API nodes 提供。视频路径使用 [`upload_video_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_video.py#L108-L138)；音频通过 [`upload_bytes_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L747-L790)把 `BytesIO` 交给 `upload_file_to_comfyapi`。

[`register_core_asset`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L715-L765)先 `source_url = await upload()`，随后调用 `create_asset`；[`create_asset`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L334-L358)发送带 `URL` 的 JSON。也可直接传现成公开 URL 跳过 Comfy.org 上传。这不是 BytePlus 原生文件入库接口。

源码的链接复用缓存设为 12 小时，并在注释中称 Comfy.org 链接约 24 小时后删除；这里只能确认项目按此假设编写，未核验 Comfy.org 当前保留政策或获得服务保证，不能把注释当作托管 SLA。[缓存实现](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L747-L790)

需避免同仓库的命名误导：[`upload_file_to_ark`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L415-L470)确实调用 `client.ark.files.create(file=...)` 上传 bytes 并返回 file ID，但上面素材入库路径没有用它作为 CreateAsset 的输入。Files API 能上传文件，不能据此推导 CreateAsset 支持 bytes 或该 file ID。

## 3. byteplus-ai-gen-platform：签名 PUT/GET 与素材入库串联

[`uploadToTos`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/api/tos.ts#L102-L125)先请求 PUT URL，浏览器执行 `fetch(url, {method: 'PUT', body: file})`，然后请求 GET URL。文件 bytes 直接进入 TOS，应用服务端负责签名。

[`uploadOneAsset`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/hooks/useAssetUpload.ts#L125-L185)上传后额外把 GET URL 重签为 12 小时，再把 `longGet.url` 传给 `createAsset`，之后轮询处理状态。[TTL 常量](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/hooks/useAssetUpload.ts#L9-L16)与[签名器](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/server/signers/tos.ts#L1-L8)分别给出素材 GET 12 小时、PUT 15 分钟；这些是代码中的选择。

配置包括 AK/SK、region、endpoint、bucket、key prefix 和默认 GET TTL；[`createTosRouter`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/server/routes/tos.ts#L39-L88)按请求中的 `creds` 创建 TOS client，因此存储账户由用户配置。注意 [`src/api/tos.ts` 实际代码](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/api/tos.ts#L34-L55)从前端 store 取 AK/SK 并随签名请求发送；文件开头「浏览器看不到 AK/SK」的旧注释与此不符，不能照抄成安全结论。所读上传/入库链路未展示 TOS 对象自动删除，签名过期也不代表对象清理。

## 4. ComfyUI-Seedance：OSS 与 tmpfiles.org 两种中转

[`_upload_image_for_asset` 及入库节点](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/nodes.py#L938-L952)把图片写为临时 PNG 并调用 `upload_file_to_public_url`；[入库调用](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/nodes.py#L1368-L1381)再把得到的 `source_url` 交给 client。

[`py/api/upload.py`](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/api/upload.py)在 OSS 配置齐全时上传 OSS，否则调用 tmpfiles.org 的 multipart 上传接口并转换下载 URL。OSS 路径先 `PUT` 文件内容，再返回默认 86400 秒有效的签名 GET URL；tmpfiles.org 是额外第三方中转服务，不是供应商素材库。该 helper 没有展示 OSS 对象删除，节点的 `finally` 清理针对本地临时文件，不代表远端文件已删除。

[`py/api/asset.py` 的 `create_asset`](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/api/asset.py)验证 HTTP(S) URL，并以 `URL` 字段请求 CreateAsset。没有核验 tmpfiles.org 的当前保留期限、配额或服务条款，不据此承诺生命周期。

## 对 ArcReel 的可选最小方案

以下保留研究时的方案比较。后续讨论已确认采用用户自有对象存储中转，并在本期以独立阶段交付新增图片的远端主存储；素材存储与供应商关联保持分离。当前采纳范围以 [ADR-0108](../adr/0108-material-storage-independent-of-provider-registration.md) 为准。

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
