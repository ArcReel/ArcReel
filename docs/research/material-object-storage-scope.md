# 素材长期对象存储的增量范围

日期：2026-11-01。背景：#2249 设计讨论。以下是基于 [`3dffb865`](https://github.com/ArcReel/ArcReel/tree/3dffb86572cf2a27d6fc984a938c1edf28e6049d) 的实施前源码评估。调研未运行测试，也未接触真实存储。正式对象如何提交、保留和归档，见 [ADR 0116](../adr/0116-remote-content-versions-saved-by-formal-commit-with-object-references.md)、[ADR 0118](../adr/0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md) 和 [ADR 0119](../adr/0119-archives-carry-content-not-storage-locators.md)。

## 评估前提

本文评估用户自有对象存储上传中转之外，新增图片内容版本采用远端主存储的增量工作；旧媒体不批量搬迁，音视频、项目文本、数据库与 Agent 工作目录不纳入这次评估。长期存储指远端承担持久保存、本地完整副本可以清理，不能仅增加云端镜像。领域决策见 [ADR 0108](../adr/0108-material-storage-independent-of-provider-registration.md)，交付范围与验收要求以 [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559) 及其子任务为准。

## 判断

可以复用中转的连接配置、凭证读取、对象上传与下载签名；真正增加的工作是持久提交、内容读取与完整生命周期。仅新增图片的远端主存储是中到大的跨流程增量，不是加一个配置项。全媒体与历史搬迁会继续显著扩大范围。此判断是静态分析，不是工期承诺。

中转对象在供应商完成读取后即可按策略清理，而正式素材对象是版本恢复、项目导出及后续生成的权威内容来源，不能套用中转对象的过期清理策略。即使两者共用同一套连接配置，也必须明确划分两类对象的保留与删除责任。

## 源码证据与增量影响

| 路径 | 基线源码证据 | 长期对象存储的增量影响 |
|---|---|---|
| 生成与上传 | [`image_backends/base.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/backends/image_backends/base.py) 的 `download_image_to_path`、`save_image_from_response_item` 与 [`upload_finalize.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/server/services/currency/upload_finalize.py) 的 `stage_uploaded_bytes` 写本地暂存文件 | 可继续本地暂存，不要求每个调用通道直接写云端；在正式提交处上传 |
| 正式版本提交 | [`formal_image_commit.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/server/services/tasks/formal_image_commit.py) 的 `_commit_staged_formal_image` 集中提交版本与清单；[`version_manager.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/artifacts/version_manager.py) 的 `commit_staged_version` 依赖本地复制、替换与失败回滚 | 本地回滚不覆盖已上传的远端对象；远端提交还涉及遗留对象清理及版本、元数据与清单的一致性，补偿与验证要求见 [#3566](https://github.com/ArcReel/ArcReel/issues/3566) |
| 读取与预览 | [`files.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/server/routers/files.py) 的 `serve_project_file` 检查本地文件与 stat；[`image_thumbnails.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/infra/image_thumbnails.py) 的 `ensure_image_thumbnail` 读取本地图片 | 根据素材内容版本取得本地缓存或可用下载地址；缓存丢失可回源，缩略图仍可本地生成 |
| 生成输入 | [`reference_compression.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/references/reference_compression.py) 的 `_try_read_image` 与 [`ark.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/backends/video_backends/ark.py) 的 `_build_image_content` 读取本地文件 | 在统一读取入口按内容版本取回、校验并准备本地工作文件；不必让每个 PIL 或 backend 调用认识对象存储 |
| 产物清单、时效与规划 | [`artifact_currency.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/artifacts/artifact_currency.py) 的 `read_artifact_content_digest` 经清单适配器读取正式内容；[`artifact_manifest.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/artifacts/artifact_manifest.py) 的 `_read_open_artifact` 读取本地文件描述符；[`artifact_planner.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/artifacts/artifact_planner.py) 的 `_track_dependency_digest` 及 [`visual_artifact_provenance.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/artifacts/visual_artifact_provenance.py) 的 `visual_references_match_snapshot` 读取本地内容摘要 | 按素材内容版本与持久定位判定；缓存缺失不等于正式素材缺失。复用可信摘要时保留内容验证与并发一致性，必要取图经统一入口处理；清缓存不改变产物时效或规划结果 |
| 版本恢复与回收站 | [`version_manager.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/artifacts/version_manager.py) 的 `restore_version` 校验快照、安装文件并回滚失败；[`project_trash.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/project/project_trash.py) 的 `record_deletion` 移动文件后提交记录，恢复经 [`trash_restore.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/lib/project/trash_restore.py) | 远端版本必须仍可取回；删除项目引用不立即删除回收站或其他引用仍需要的对象；恢复仍先校验内容再选中版本 |
| 归档与跨项目复制 | [`project_archive.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/server/services/project/project_archive.py) 的 `_prepare_export_snapshot`、`_capture_stable_visible_tree` 生成一致的本地归档快照；[`assets.py`](https://github.com/ArcReel/ArcReel/blob/3dffb86572cf2a27d6fc984a938c1edf28e6049d/server/routers/assets.py) 的 `_copy_into_global_pool`、`apply_to_project` 复制文件并提交版本与清单 | 导出包取回固定版本并包含完整内容，不能只打包临时 URL；导入与全局资产应用仍保证目标独立可用，来源删除不破坏目标 |

这些读点依赖本地文件，因而远端存储的增量不仅是上传：还涉及内容校验、缓存并发与回源失败，以及正式提交与上传结果的区分。

## 评估边界与证据限制

将范围限定为新增图片内容版本并保留本地暂存与工作缓存，虽能收敛写入入口的改动量，但无法绕开版本管理、回收站、归档、全局资产应用及时效判定等环节的适配。存储开关按新提交内容版本生效、旧版本恢复沿用原定位的具体行为见 [#3571](https://github.com/ArcReel/ArcReel/issues/3571)。

基线中的相关测试入口包括 `tests/unit/lib/artifacts/test_version_manager.py`、`tests/integration/server/routers/test_files_router_asset_sheet_upload.py`、`tests/integration/server/routers/test_project_trash_router.py` 及 `tests/integration/server/services/project/test_project_archive_service*.py`。这些测试可作为后续验证的扩展起点，但目前并不能证明远端存储方案已成立；本次调研未运行功能测试，也未访问真实存储桶。正式验收以 Spec 和实现任务为准，不以静态评估替代运行证据。
