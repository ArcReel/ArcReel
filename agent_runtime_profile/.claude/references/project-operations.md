# 项目数据操作

## 编辑剧本与项目

- 编辑剧本前，用 `get_episode_script` 读正文和 revision，再用 `patch_episode_script` 传入原样的 `base_revision` 与有序 `operations[]`（`update` / `insert` / `move` / `remove` / `split`）。工具先预检，再原子提交。失败时按 `operation_index` 和字段位置修正。revision 冲突时，重读最新内容后再调整操作。
- 集标题用 `patch_episode_meta` 修改。资产定义等项目设置用 `patch_project` 修改。
- 修改 `image_prompt` 或 `video_prompt` 后，已付费产物仍可用。向用户说明现有画面依据已变，由用户决定是否按新提示词重新生成。

## 编辑源文

旁白/解说和剧情演绎项目用 `upload_source` 新增文件或用 `on_conflict=replace` 整份替换，用 `edit_source_text` 修改片段或全文。

整本源文按 `whole_source_files` 的顺序读取；直接放入 `source/` 目录的未登记文件须先经 `upload_source` 登记。切出集的 `source/episode_{集 ID}.txt` 由分集账本派生，下一批分集会从最后一个切出集的结尾接续。若用户已自行拆好单集原文，则用 `upload_source` 的 `role=episode` 逐集登记，之后可直接进行脚本规划。剧情演绎项目的 `source_kind` 记录在源文清单项或自带原文的集条目中；切出集取原文范围起点所在文件的类型，字段缺失时按小说处理。

修改整本源文可能会触发切出集的重映射。当工具返回受影响集清单和 `revision` 时，须先将影响告知用户，待用户确认后再携带同一 `revision` 重新发送请求。删除或调整整本源文文件的顺序，以及在外部修改文件后更新分集账本，均由用户在 Web 端「分集」视图中操作。

## 回收站

1. 用户明确要求删除未引用的文件或非当前历史版本时，调用 `delete_project_file` 移入项目回收站，默认保留 30 天。被引用的文件会被拒绝，引导用户在所属界面先处理引用。媒体目录和 `versions/` 只经项目工具修改。
2. `patch_episode_script` 的 `remove` 操作、正式剧本覆盖以及 `merge_asset` 会将被移除的内容及其对应的媒体或版本历史写入删除记录；通过 `upload_source` 替换掉的旧源文件同样支持恢复。
3. 恢复前用 `list_trash_records` 列出记录，确认目标后调用 `restore_trash_record`。恢复只新增，分镜和视频单元始终分配新编号。名称冲突时分配新名称。后续工具调用使用结果中返回的名称或编号，向用户转述时称资产名称和条目在本集的位置。恢复被并资产不会改回已重写的引用。
4. 若返回 `degraded=true`，需告知用户已恢复的文件情况，并引导用户前往 Web 端「文件」视图下载 `download_only` 列出的结构文件。
5. 清空回收站和彻底删除由用户在 Web 端「文件」视图操作，Agent 无权代为执行（包括通过 HTTP 接口调用）。

## 供应商素材库

火山方舟和 BytePlus 素材库均通过 `mcp__arcreel__*` 素材库工具操作。

- 用 `get_provider_asset_links` 查询图片当前版本的关联关系、入库状态与可用账号；用 `list_provider_library_assets` 浏览指定账号下的素材。用户选定图片后，用 `select_provider_library_asset` 将其保存为资产图或角色衍生图的新版本并建立关联；经用户同意后，可用 `release_provider_asset_link` 解除关联，两侧图片都会保留。
- 收到 `provider_input_image_rejected` 或 `provider_input_image_rejected_at` 时，向用户说明审核拒绝及可选的入库处理。只有用户明确要求上传指定图片时才调用 `upload_to_provider_asset_library`，传入 `user_requested=true`。等待时按用户需要查 `get_provider_asset_links`，不反复轮询。状态为 `active` 后由用户决定是否重新生成。
- 收到 `provider_asset_link_processing` 或 `provider_asset_link_failed` 时，向用户报告当前正在入库或具体失败原因，再请用户选择继续等待、解除关联后重传，或改发普通图片。禁止自动解除关联、重传、重新生成或更换供应商。
- 素材库账号、对象存储与推理凭证的绑定由用户在 Web 端「素材库与存储」页面配置，工具结果不会返回密钥。素材重命名和删除则由用户在供应商控制台完成。
