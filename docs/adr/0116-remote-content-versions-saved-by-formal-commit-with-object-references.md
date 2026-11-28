---
status: accepted
---

# 正式提交选择图片版本的存储位置，正式对象按内容摘要共享

新写入的图片经 [formal_commit](../../lib/material/formal_objects.py) 按实例设置保存在本地或对象存储。远端保存与确认发生在写版本或定位记录前，失败时正式写入回滚。版本化图片通过版本提交接入，无版本项目图片通过独立项目媒体定位表接入（见 [ADR 0132](0132-project-media-storage-locators-decoupled-from-version-history.md)），使生成、编辑、上传和导入共用同一套提交语义。选择或恢复已有版本沿用其定位，视频与音频继续保存在本地。

远端版本定位记录写入时的连接、桶与对象 key。读取按记录核对连接和桶，签名地址不写入定位。正式对象与中转副本使用独立前缀。同一连接内同字节的素材共享对象，提交先登记占用，结束后结算为活跃引用或撤下占用。进程中断后启动补做结算。零引用回收与供应商直接取正式对象时的持有期由 [ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md) 定义。项目路径与全局资产库上的远端工作文件只是缓存，读取契约见 [ADR 0117](0117-evicted-work-files-count-as-present-by-recorded-digest.md)。

对象存储连接统一收敛到标准 S3 兼容协议实现并保持实例级单活跃连接，不再按云厂商维护多套私有 SDK，也不引入多条只读退役连接长期并存。同目标仅轮换访问密钥时保留原连接身份；真正换桶或换 Endpoint 分配新连接身份。旧桶无在用正式对象时，按用户勾选清理或放弃待删对象、中转副本与仅依赖旧桶的外部取图租约后直接切换或删除；旧桶仍有项目、回收站、已删除项目或全局资产库引用的正式对象时，通过幂等可重试的「收敛回本地」流程把在用远端图片逐一落盘核验并转为永久本地文件后再切换或删除。收敛期间后台回收与全局缓存清理服从用户是否保留旧桶文件的选择。旧桶永久失联执行强制断开时，先按内容摘要跨持有方抢救所有尚存的有效本地工作文件转为本地文件。

连接变更、存储目标变更、正式提交登记和外部取图签名共用连接锁，正式对象处置与后台回收还按对象串行。对象上传发生在版本锁或项目锁内，会延后同项目其他写入。两种锁都以单进程部署为前提，见 [ADR 0083](0083-generation-queue-stays-in-house-for-now.md)。

范围与验收见 [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559)、[Spec #4204](https://github.com/ArcReel/ArcReel/issues/4204) 与 [Spec #4206](https://github.com/ArcReel/ArcReel/issues/4206)。
