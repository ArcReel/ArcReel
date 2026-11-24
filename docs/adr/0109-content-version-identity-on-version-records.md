---
status: accepted
---

# 内容版本身份、摘要与持久定位记在版本记录上

项目版本记录是内容版本的身份与保存事实。用独立的随机 ID 标识内容版本，用摘要校验快照，用持久定位找到快照。多条记录即使字节相同也有独立的身份，否则解除一份供应商素材关联会牵连另一份。项目内改名、改选版本与恢复历史版本都沿用原有身份，跨项目使用身份时须带上归属项目。

- `content_version_id` 是内容版本身份，`content_digest` 是快照的 SHA-256。旧记录在需要内容版本身份时由 `VersionManager.ensure_content_version` 补写，避免升级时遍历全部历史媒体。
- 无 `storage` 字段时，`file` 是本地快照定位。远端版本的持久定位由 [ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md) 规定，选中后的正式文件可以是工作副本。工作文件见 [ADR 0117](0117-evicted-work-files-count-as-present-by-recorded-digest.md)。
- 读取或恢复版本时按已有摘要校验。旧记录没有摘要时仍可读取。归档保留包内版本记录的身份，但将内容带到目标实例重新保存，见 [ADR 0119](0119-archives-carry-content-not-storage-locators.md)。

这使供应商素材关联可以依附内容版本，存储位置和资产文件名则可独立变化，见 [ADR 0108](0108-material-storage-independent-of-provider-registration.md)。
