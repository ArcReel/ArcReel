---
status: accepted
---

# 项目媒体存储定位与版本管理解耦

「媒体文件保存在本地还是对象存储」与「该媒体是否拥有多版本历史」是正交的两个维度。[ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md) 首期将远端保存接缝放在版本管理器中，使没有多版本历史的分镜尾帧、角色原图、商品原图与风格参考图在启用对象存储后仍硬编码落在本地，且商品多图列表与多种原始扩展名也不适合强行塞进 1 对 1 固定 `.png` 的版本历史模型。

我们在项目现有本地 SQLite 元数据库中新增独立于版本历史表的「项目媒体存储定位」表，以项目内规范相对路径为键记录无版本媒体的内容身份、SHA-256 摘要、规范化扩展名与持久存储定位。版本化媒体继续以版本历史表为唯一权威，无版本媒体以新定位表为唯一权威，两者互不重叠，由素材存储层对外统一提供查询、枚举、工作文件按需取回与缓存清理（[ADR 0117](0117-evicted-work-files-count-as-present-by-recorded-digest.md)）、正式提交结算与引用重算（[ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md)）、连接收敛回本地（[ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md)）及项目归档导入导出（[ADR 0119](0119-archives-carry-content-not-storage-locators.md)）。

路径占用判定（如商品原图序号分配、资产重命名与远端尾帧删除）统一在锁内同时核对逻辑定位表与磁盘文件，避免缓存清理后出现序号碰撞或漏迁。项目回收站范围保持不变（尾帧删除、分镜删除、整资产删除与脚本覆盖仍进回收站并保留远端定位快照）；单张清除或替换角色原图、商品原图与风格参考图时不另建结构化回收站恢复流程，改为在锁内同步移除旧定位与本地文件并释放引用，避免遗留孤儿文件与对象泄漏。

范围与验收见 [Spec #4206](https://github.com/ArcReel/ArcReel/issues/4206)。
