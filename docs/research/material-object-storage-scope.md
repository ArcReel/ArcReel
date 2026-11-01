# 素材长期对象存储的增量范围

日期：2026-11-01。背景：#2249 设计讨论。评估基线为 `origin/main` 的 `3dffb86572cf2a27d6fc984a938c1edf28e6049d`；本地未切换分支，对有差异的相关文件读取该提交。审查时复核至 `5133bbb9acb50e011b91766d9021385a40b723e3`，两基线之间仅依赖锁文件变化，本文相关代码未变。本文仅作源码范围评估，未实现、未运行测试或访问真实存储桶。

## 已确定范围

已确定本期交付用户配置的对象存储上传中转，完成本地图片到供应商素材库的入库流程。素材存储与供应商素材关联独立，关联归属具体内容版本，见 [ADR 0108](../adr/0108-material-storage-independent-of-provider-registration.md)。

已确定将下文限定范围的新增图片远端主存储作为本期独立阶段交付，决策记录见 ADR 0108。本文将「长期对象存储」理解为远端保存持久内容，本地工作缓存可以在不使用时清理、之后重新取回；只把完整本地文件再复制到云端不算实现这一目标。项目元数据、数据库和 Agent 工作目录不因此迁到对象存储。

## 判断

可以复用中转的连接配置、凭证读取、对象上传与下载签名；真正增加的工作是持久提交、内容读取与完整生命周期。仅新增图片的远端主存储是中到大的跨流程增量，不是加一个配置项。全媒体与历史搬迁会继续显著扩大范围。此判断是静态分析，不是工期承诺。

中转对象在供应商完成读取后可按策略清理；正式素材对象是恢复、导出及后续生成的内容来源，不能套用中转对象的过期清理策略。即使共用一个连接配置，也必须分清这两类对象的保留与删除责任。

## 关键路径与新增工作

| 路径 | 现状证据 | 长期对象存储新增工作 |
|---|---|---|
| 生成与上传 | [`image_backends/base.py`](../../lib/backends/image_backends/base.py) 的 `download_image_to_path`、`save_image_from_response_item` 与 [`upload_finalize.py`](../../server/services/currency/upload_finalize.py) 的 `stage_uploaded_bytes` 写本地暂存文件 | 可继续本地暂存，不要求每个调用通道直接写云端；在正式提交处上传 |
| 正式版本提交 | [`formal_image_commit.py`](../../server/services/tasks/formal_image_commit.py) 的 `_commit_staged_formal_image` 集中提交版本与清单；[`version_manager.py`](../../lib/artifacts/version_manager.py) 的 `commit_staged_version` 依赖本地复制、替换与失败回滚 | 对象上传成功后才发布新版本引用；上传失败保持旧版本。可恢复地跟踪本次新建对象定位，正式提交失败后将确认无有效引用的对象纳入待清理记录并尝试删除，删除失败保留定位与原因供重试；不删除复用或仍被引用的对象，保留版本、元数据与清单的一致性 |
| 读取与预览 | [`files.py`](../../server/routers/files.py) 的 `serve_project_file` 检查本地文件与 stat；[`image_thumbnails.py`](../../lib/infra/image_thumbnails.py) 的 `ensure_image_thumbnail` 读取本地图片 | 根据素材内容版本取得本地缓存或可用下载地址；缓存丢失可回源，缩略图仍可本地生成 |
| 生成输入 | [`reference_compression.py`](../../lib/references/reference_compression.py) 的 `_try_read_image` 与 [`ark.py`](../../lib/backends/video_backends/ark.py) 的 `_build_image_content` 读取本地文件 | 在统一读取入口按内容版本取回、校验并准备本地工作文件；不必让每个 PIL 或 backend 调用认识对象存储 |
| 产物清单、时效与规划 | [`artifact_currency.py`](../../lib/artifacts/artifact_currency.py) 的 `read_artifact_content_digest` 经清单适配器读取正式内容；[`artifact_manifest.py`](../../lib/artifacts/artifact_manifest.py) 的 `_read_open_artifact` 读取本地文件描述符；[`artifact_planner.py`](../../lib/artifacts/artifact_planner.py) 的 `_track_dependency_digest` 及 [`visual_artifact_provenance.py`](../../lib/artifacts/visual_artifact_provenance.py) 的 `visual_references_match_snapshot` 读取本地内容摘要 | 按素材内容版本与持久定位判定；缓存缺失不等于正式素材缺失。复用可信摘要时保留内容验证与并发一致性，必要取图经统一入口处理；清缓存不改变产物时效或规划结果 |
| 版本恢复与回收站 | [`version_manager.py`](../../lib/artifacts/version_manager.py) 的 `restore_version` 校验快照、安装文件并回滚失败；[`project_trash.py`](../../lib/project/project_trash.py) 的 `record_deletion` 移动文件后提交记录，恢复经 [`trash_restore.py`](../../lib/project/trash_restore.py) | 远端版本必须仍可取回；删除项目引用不立即删除回收站或其他引用仍需要的对象；恢复仍先校验内容再选中版本 |
| 归档与跨项目复制 | [`project_archive.py`](../../server/services/project/project_archive.py) 的 `_prepare_export_snapshot`、`_capture_stable_visible_tree` 生成一致的本地归档快照；[`assets.py`](../../server/routers/assets.py) 的 `_copy_into_global_pool`、`apply_to_project` 复制文件并提交版本与清单 | 导出包取回固定版本并包含完整内容，不能只打包临时 URL；导入与全局资产应用仍保证目标独立可用，来源删除不破坏目标 |

本地缓存还需要内容校验、并发读取协调、正在使用时不驱逐与可恢复下载失败，并提供实际可用的清理入口或策略。对象上传完成不能直接等同于正式版本提交成功。

## 可控制的交付范围

已选择以下范围；全局存储默认本地，用户显式启用远端后影响后续新增图片，切换不搬迁已有内容，具体规则见 ADR 0108：

- 存储选择按新提交的图片内容版本生效，包含已有素材重新生成、编辑或上传得到的新版本；选择或恢复已有版本仍按原定位读取。本地和远端版本可并存，旧图片与历史版本不主动搬迁。
- 继续允许本地暂存和工作缓存，完成后可清理不再使用的缓存，不承诺运行时完全不落磁盘。
- 项目元数据和数据库继续本地保存；音视频与成片的远端主存储另行处理。
- 即使不搬历史媒体，也完整适配图片的版本切换、回收站、归档、全局资产应用与生成消费，不能只完成上传和预览。
- 与供应商入库共用对象存储连接配置，但正式素材不进入中转副本的自动清理范围。

这种范围作为同一批工作的独立阶段交付，避免把「上传中转完成」当成「长期存储完成」。只保留云端镜像而无法清理本地持久副本是更小的改动，但不能据此宣称已实现远端主存储或节省本地磁盘。

## 验证范围

实施时至少证明：远端上传失败不发布新版本；对象上传成功而正式提交失败时旧版本不变，本次新建且无引用的对象被删除或留有可重试清理记录，补偿不误删共享对象；清除未使用的本地缓存后预览和生成仍可取图，产物时效与工作流规划不因缓存缺失而误判；版本恢复与回收站恢复不依赖被清理的缓存；归档在另一实例导入后内容完整；全局资产应用后删除来源不破坏目标；存储位置变化不改变内容身份或供应商关联。

现有相关测试入口包括 `tests/unit/lib/artifacts/test_version_manager.py`、`tests/integration/server/routers/test_files_router_asset_sheet_upload.py`、`tests/integration/server/routers/test_project_trash_router.py` 及 `tests/integration/server/services/project/test_project_archive_service*.py`。这些测试目前不能证明远端存储成立，新增路径需在实施阶段补充故障与回源验证。本轮不运行与文档评估无关的应用测试。
