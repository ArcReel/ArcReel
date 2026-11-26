# 旧发行版导出包

`v0.37.0-tts.zip` 由 ArcReel 标签 `v0.37.0`（提交 `afdb2091af79c154f87c3a1683bfb5d2ddd75493`）的 `ProjectArchiveService.export_project` 生成。该包用于验证已有项目升级。测试使用已提交的 ZIP，可离线运行。

## 生成与核验

1. 从标签提取源码，将 [schema 20 项目快照](../v20-tts-exports.zip)原样解压至空数据根目录下的 `projects/tts/`。输入 ZIP 的 SHA-256 为 `cd935bbd6257daa021b36c1821f7eae2507bd99fce8a1db9381d2e07266a5850`。
2. 在标签的源码目录中初始化 `ProjectManager(data_root)`，调用 `ProjectArchiveService(manager).export_project("tts", scope="full")`，保存返回的 ZIP。归档信封记录 `format_version=2` 和 `scope="full"`，包内项目的 `schema_version=20`。导出时间写入信封，重复导出的整包摘要会变化。
3. 本次导出包含两条分镜、两段视频和两段旁白。核对输入与归档，11 个 PNG、MP4、WAV 文件（含历史版本）的相对路径和字节逐一相同。第一段旁白的两个历史版本内容不同，当前音频与第 2 版相同。分镜 PNG 路径存放可区分归属的测试字节。旧导出器会规整剧本，完整包中未收录快照的 renders 目录。
4. [升级回归](../../integration/server/services/project/test_legacy_media_preservation.py)固定整包摘要，从原包取媒体期望值，直接通过归档入口导入，再验证逐文件字节、旁白版本与时效以及二次迁移。单独运行 `uv run python -m pytest -k test_released_v037_full_archive_upgrades tests/integration/server/services/project/test_legacy_media_preservation.py`。

## 其他旧项目样本

| 样本 | 来源与用途 |
| --- | --- |
| `v15-tts.zip`、`v20-tts-exports.zip` | 提交 `73f1a958d` 纳入的项目树快照。前者采用 v0.31.0 音频和转场、现行字幕计时规则，后者由 v0.37.0 渲染。两者均无导出信封。 |
| `perf_before/` | 提交 `847b7e18d` 的重构前行为基线。`legacy-v1.zip` 是 schema 1 项目树，`workflow-v2.zip` 是 schema 21 工作流基线，`current.zip` 与 `full.zip` 是 schema 21 归档。该组样本按基线提交追踪。 |
| `tests/legacy_project_shapes.py` | 构造器可用当前代码指定 schema、推进部分迁移、调整绑定或制造路径冲突。属性测试从这些构造器、冻结的 schema 1 项目树及旧导出包中的剧本派生小项目，按例重建项目目录与项目 SQLite 历史库。 |
