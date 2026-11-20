---
status: accepted
---

# 项目归档携带内容字节和可用的供应商素材关联

目标实例必须能够独立完成归档导入。归档按选定范围保留正式内容和版本快照，导出时取回所需远端工作文件并按摘要核验。包内不带源实例的连接、桶、对象 key、签名地址和凭证。导入忽略所有来包的对象存储定位，按目标实例当前设置保存图片。允许归档指定桶内对象会把任意来包变成目标实例的取图指令。

旧归档在安装前由归档服务于暂存目录修复，复用规范资源路径、剧本骨架和项目迁移链。归档修复会处理旧目录布局、丢失路径或残缺记录，已安装项目的保存入口负责校验现有内容，两处各有契约。[ADR 0098](0098-backfill-keeps-existing-content-gaps-only-gate-generation.md) 定义哪些结构应修复、哪些项目须拒绝。

仅当前版本的包裁剪了生成图片的历史。导入远端存储时为包内没有版本记录的正式图片建立新内容版本，尾帧等始终保存在本地的图片除外。旧快照与版本摘要不符时仍可按本地版本保存。远端保存失败时项目不安装，提交占用由统一结算处理。覆盖导入旧项目的正式对象随墓碑保留，见 [ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md)。

归档只带包内版本选定且可用的供应商素材关联。导入按版本身份与摘要核对实际字节，匹配后复制关联及冻结的调用范围，保留目标范围内已有的选定关联。未完成任务依赖源实例后台和中转资源，失败或已解除的关联也无可用内容。关联还原失败时报告警告，已导入图片仍可使用。运行时以推理凭证实际绑定的云账号判断是否选中关联，云账号资格由供应商决定，见 [ADR 0113](0113-generation-selects-provider-assets-by-actual-inference-credential.md) 和 [ADR 0111](0111-asset-upload-job-and-link-share-one-record.md)。

项目导入导出入口见 [project_archive.py](../../server/services/project/project_archive.py)。范围与验收见 [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559)。
