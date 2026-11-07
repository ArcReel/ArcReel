# Investigation of local-file upload paths for CreateAsset

> Date: 2026-11-01. Context: #2249. Explain how URL-only CreateAsset receives local materials, distinguishing long-term storage from temporary preregistration relay. Scope: pinned source versions of four open-source projects. No credentials were configured, and no uploads, CreateAsset calls, or deletion tests were executed.

## Findings

All four registration paths follow local bytes → provider-readable HTTP(S) URL → CreateAsset. No implementation directly sends bytes, multipart, or Base64 to CreateAsset. Relays differ: configured TOS/S3, Comfy.org hosting, or OSS/tmpfiles.org. Chains appear below with source links.

Volcengine's official [CreateAsset documentation](https://docs.volcengine.com/docs/ark/create-asset-api?lang=zh) requires public-download `URL`, rejecting Base64; processing is asynchronous. Distinguish returned material IDs from completed provider downloads/processing. Temporary URLs must cover asynchronous retrieval. Six-/twelve-hour values below are project choices, not provider SLAs.

| Project/pinned version | Local-file path into material libraries | Byte recipient | Finding |
|---|---|---|---|
| `byteplus-sa/ark-mcp`, `4ae9fbc31c98246607f83d32d4381b6e27305666` | `media_upload` uploads TOS/S3; `ark_asset_create` signs object keys as GET URLs before registration | Configured TOS/S3 bucket | Object-storage relay |
| `byteplus-sa/ComfyUI-BytePlus-ModelArk`, `d3a91b9c8bb314ea1a1327215378d62a72d6a20d` | ComfyUI image/video/audio → Comfy.org helper → URL → CreateAsset | Comfy.org upload service | Third-party hosted relay |
| `IanLin419/byteplus-ai-gen-platform`, `9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2` | Server signs PUT; browser uploads to TOS; signs GET; CreateAsset | User-configured TOS bucket | Browser-to-storage, not direct CreateAsset upload |
| `magicwang1111/ComfyUI-Seedance`, `896728c266a6831286641c2b436d563aeaf8982a` | Image → temporary PNG; configured OSS or fallback tmpfiles.org; URL → CreateAsset | OSS/tmpfiles.org | Object-storage/third-party relay |

## 1. ark-mcp: separate upload from provider registration

[`prepare_upload` and `upload_one` in `media_upload.py`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/media_upload.py#L126-L202) distinguishes local paths/decoded Base64, calling `gateway.upload_file` / `gateway.upload_bytes`, then `presign_get`, returning URLs, keys, expiry. The MCP tool accepts Base64, not CreateAsset.

[`make_object_storage_gateway`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/providers/object_storage.py#L69-L91) selects S3/TOS by configuration, requiring access keys, secret keys, buckets; [`TosGateway`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/providers/tos/client.py#L60-L107) uploads/signs GETs. Repository [S3 storage documentation](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/docs/s3-object-storage.md) explicitly uses private buckets with temporary presigned read access, requiring deployer storage configuration rather than free author hosting.

[`ark_asset_create`](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/ark_asset_create.py#L29-L100) accepts URLs or object keys; keys are resigned with `_SOURCE_URL_TTL_SECONDS = 6 * 3600` before `service.create_asset(url=...)`. Do not conflate default upload signing with registration TTL. Inspected upload/sign/register functions show no automatic object deletion; URL expiry is not evidence that the object was deleted.

## 2. ComfyUI-BytePlus-ModelArk: reuse Comfy.org uploads

[`upload_image_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L204-L221) calls built-in `upload_image_to_comfyapi`, explicitly requiring Comfy.org login and API-node helpers. Videos use [`upload_video_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_video.py#L108-L138); audio passes `BytesIO` to `upload_file_to_comfyapi` through [`upload_bytes_to_comfy_storage`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L747-L790).

[`register_core_asset`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L715-L765) first executes `source_url = await upload()`, then `create_asset`; [`create_asset`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_assets.py#L334-L358) sends JSON containing `URL`. Existing public URLs can bypass Comfy.org. This is not BytePlus-native file registration.

Sources cache link reuse for twelve hours, commenting that Comfy.org links disappear after approximately 24. This confirms implementation assumptions only, without verifying current retention/guarantees; comments are not hosting SLAs. [Cache implementation](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L747-L790)

Avoid misleading names: [`upload_file_to_ark`](https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/blob/d3a91b9c8bb314ea1a1327215378d62a72d6a20d/nodes/nodes_shared.py#L415-L470) uploads bytes through `client.ark.files.create(file=...)`, returning file IDs, but registration above does not use it for CreateAsset input. File-upload support in Files API proves neither CreateAsset bytes nor file-ID support.

## 3. byteplus-ai-gen-platform: signed PUT/GET followed by registration

[`uploadToTos`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/api/tos.ts#L102-L125) requests PUT URLs, performs browser `fetch(url, {method: 'PUT', body: file})`, then requests GET URLs. Bytes go directly to TOS; servers sign.

[`uploadOneAsset`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/hooks/useAssetUpload.ts#L125-L185) re-signs uploaded GETs for twelve hours, passes `longGet.url` to `createAsset`, then polls. [TTL constants](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/hooks/useAssetUpload.ts#L9-L16) and [Signer](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/server/signers/tos.ts#L1-L8) specify twelve-hour material GETs/fifteen-minute PUTs, choices encoded in this project.

Configuration includes AK/SK, region, endpoint, bucket, key prefixes/default GET TTL. [`createTosRouter`](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/server/routes/tos.ts#L39-L88) creates TOS clients from request `creds`, making storage user-configured. Actual [`src/api/tos.ts` code](https://github.com/IanLin419/byteplus-ai-gen-platform/blob/9ea9e2a92e39d4adb86e6f6f7cd6a138ad94caa2/src/api/tos.ts#L34-L55) reads AK/SK from frontend stores and sends them with signing requests, contradicting old "browsers cannot see AK/SK" comments, which must not become security conclusions. Inspected paths show no automatic TOS deletion; signature expiry is not cleanup.

## 4. ComfyUI-Seedance: OSS or tmpfiles.org relays

[`_upload_image_for_asset` and registration nodes](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/nodes.py#L938-L952) writes temporary PNGs and calls `upload_file_to_public_url`; [Registration call](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/nodes.py#L1368-L1381) passes resulting `source_url` to clients.

[`py/api/upload.py`](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/api/upload.py) uses OSS when fully configured, otherwise multipart tmpfiles.org upload with converted download URLs. OSS first `PUT`s bytes, returning signed GETs defaulting to 86400 seconds. tmpfiles.org is another relay, not provider libraries. Helpers show no OSS deletion; node `finally` cleans local temp files, not remote ones.

[`create_asset` in `py/api/asset.py`](https://github.com/magicwang1111/ComfyUI-Seedance/blob/896728c266a6831286641c2b436d563aeaf8982a/py/api/asset.py) validates HTTP(S) URLs and calls CreateAsset through `URL`. Current tmpfiles.org retention/quotas/terms were not verified, so no lifecycle guarantee follows.

## Optional minimal ArcReel approaches

Retain research-time comparisons below. Later discussion selected user-owned object-storage relays and a separate stage for new-image remote primary storage in this delivery, keeping storage separate from provider links. [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559) governs scope; [ADR-0108](../adr/0108-material-storage-independent-of-provider-registration.md) explains independent modeling.

| Option | Minimal path | Decisions needed |
|---|---|---|
| Existing public URLs only | User-supplied provider-readable URL → CreateAsset → provider link | No automatic local registration yet, or user-supplied relay URLs |
| Optional user-owned temporary TOS relay | Original local storage unchanged; upload copy → sign GET → CreateAsset → track terminal state → explicitly clean copy | Accept credential setup, traffic/costs, leftovers, cleanup responsibility? |
| Existing public media URLs | Deployment supplies sufficiently long-lived downloadable URLs → CreateAsset | Actual public reachability, authentication, lifetime covering asynchronous retrieval |

The second can remain transport-only without migrating primary materials to TOS or delivering complete long-term storage. ark-mcp's separate upload/registration illustrates this boundary. Upload/expiry/cleanup mechanisms remain necessary despite "temporary" naming. [Implementation](https://github.com/byteplus-sa/ark-mcp/blob/4ae9fbc31c98246607f83d32d4381b6e27305666/src/ark_mcp/tools/ark_asset_create.py#L29-L100)

For temporary relays, start with one user-controlled backend, explicit configuration/enablement, recorded temporary identities, terminal-state cleanup, and retry policies; never copy silent anonymous-host fallback. Validate asynchronous provider reads/retries before cleanup timing: neither returned IDs nor URL expiry justify deletion. Sources demonstrate relay patterns, not shortest safe ArcReel URL lifetimes, deletion points, or production reliability.

## Evidence limits

- Pinned source establishes implementation intent/arguments; projects were not run, and regions/permissions/costs/compatibility/success rates were not tested.
- BytePlus projects illustrate uploads, not identical Volcengine-China authentication/endpoints/material permissions; official Volcengine docs govern CreateAsset inputs.
- Only listed upload/sign/register paths were inspected, without repository-wide cleanup audits. "No automatic deletion shown" does not prove every deployment lacks cleanup.
