# 旧发行版导出包

`v0.37.0-tts.zip` 由 ArcReel `v0.37.0` 标签（提交 `afdb2091af79c154f87c3a1683bfb5d2ddd75493`）下的 `ProjectArchiveService.export_project` 生成，用于验证旧项目升级。测试直接读取已提交的 ZIP 文件，可离线运行。

## 生成与核验

1. 检出 `v0.37.0` 标签源码，将 [schema 20 项目快照](../v20-tts-exports.zip)原样解压至空数据根目录下的 `projects/tts/`。输入 ZIP 的 SHA-256 为 `cd935bbd6257daa021b36c1821f7eae2507bd99fce8a1db9381d2e07266a5850`。
2. 在该版本源码下初始化 `ProjectManager(data_root)`，调用 `ProjectArchiveService(manager).export_project("tts", scope="full")` 并保存导出的 ZIP。归档信封记录 `format_version=2` 和 `scope="full"`，包内项目的 `schema_version=20`。信封会写入导出时间，因此重复导出时整包摘要会变化。
3. 导出包内含两条分镜、两段视频和两段旁白配音。比对输入快照与导出包，11 个 PNG、MP4、WAV 文件（含历史版本）的相对路径和字节完全一致：第一段旁白配音的两个历史版本内容不同，当前音频取第 2 版；分镜 PNG 存放可区分字段归属的测试字节。旧版导出器在导出时会规整脚本数据，完整导出包不包含输入快照中的 `renders/` 目录。
4. [升级回归用例](../../integration/server/services/project/test_legacy_media_preservation.py)校验整包 SHA-256 摘要，从原包读取媒体期望字节后直接经归档入口导入，依次验证逐文件字节、旁白配音版本与产物时效，以及二次迁移后的状态稳定。单独运行：`uv run python -m pytest -k test_released_v037_full_archive_upgrades tests/integration/server/services/project/test_legacy_media_preservation.py`。

## 其他旧项目样本

| 样本 | 来源与用途 |
| --- | --- |
| `v15-tts.zip`、`v20-tts-exports.zip` | 提交 `73f1a958d` 纳入的项目目录树快照，均不含归档信封。前者采用 `v0.31.0` 的音频与转场结构及现行字幕计时规则，后者由 `v0.37.0` 渲染生成。 |
| `perf_before/` | 提交 `847b7e18d` 建立的重构前行为基线（按该基线提交溯源）：`legacy-v1.zip` 为 schema 1 项目目录树，`workflow-v2.zip` 为 schema 21 工作流基线，`current.zip` 与 `full.zip` 为 schema 21 导出归档。 |
| `tests/legacy_project_shapes.py` | 用当前代码按指定旧 schema 构造项目样本，支持只运行部分迁移步、调整文件绑定或制造路径冲突。属性测试基于这些构造器、冻结的 schema 1 项目目录树及旧导出包中的脚本派生测试项目，在每个生成样例中独立重建项目目录与项目 SQLite 历史数据库。 |
