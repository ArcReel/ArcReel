---
status: accepted
---

# 付费产物先保存历史，再更新当前版本

供应商返回结果时，创作者可能已修改生成依据或改选了当前版本。直接选中新结果会覆盖其间的修改，将历史保存与当前选中一并回滚又会丢掉已付费产物。因此，付费版本提交在版本锁内先持久化历史记录，再决定是否设为当前选中。若选中检查或写入失败，已提交的历史仍予保留。包含远端正式对象的图片，须先完成对象保存与确认，再提交历史记录，见 [ADR 0109](0109-content-version-identity-on-version-records.md) 与 [ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md)。

普通生成和接续的视频共用同一选中入口。只有提交时的父版本与生成依据仍与当前状态匹配时，才在锁内更新正式文件、选中指针和产物清单。若依据缺失、损坏或不匹配，结果仅保留在历史中。选中之后还需写回剧本和缩略图，若此阶段失败，只补偿本次选中带来的变更，避免撤下其他操作后来选中的版本。

接续视频的完成回调在工作线程中读取剧本并计算指纹，须等回调完成后才允许进入选中补偿，避免客户端收到已撤销结果的成功事件。普通生成任务由外层执行器在提交完成后发布成功事件。发布异常向上报告，已写入的产物仍然有效。事件发布同样在工作线程中执行，并继承 Worker 的事件来源上下文。提交后的取消不会打断等待，也不会把任务改判为中断。

同一资源的生成准入与选中结算共用跨进程文件锁（`generation_admission_lock` / `generation_admission_lock_sync`），锁键仅按 `(project_name, resource_id)` 计算，刻意不纳入 `script_file`（两套入口接受该参数但不参与锁键生成）。分集改绑到其他剧本路径后，新路径上的任务入队或选中仍与旧路径尚未结束的选中补偿串行，避免两条路径同时操作同一资源。历史版本删除同样依赖这把准入锁保护回退目标：`delete_history_version` 在已持有项目锁与版本锁的前提下执行，试图删除当前版本直接返回 `version_delete_current`，删除历史版本时以非阻塞方式获取该资源的生成准入锁，发生争用即返回 `version_delete_in_use`，避免与先持准入锁再取项目锁和版本锁的结算路径形成相反锁序互等；待占用方释放准入锁后，重新发起删除即可取得该锁，并在持锁期间将历史版本移入项目回收站。

实现入口见 [付费版本提交](../../lib/artifacts/version_manager.py)、[生成准入锁](../../lib/generation/generation_admission.py)、[视频选中](../../lib/artifacts/video_artifact_commit.py) 和 [视频收尾](../../server/services/currency/video_artifact_currency.py)。
