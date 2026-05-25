---
status: proposed
---

# 导入修复留在 archive，不走保存统一入口；泄漏的形状常量收敛到既有真相源

`project_archive` 的导入路径会对用户上传的、可能残缺/错乱的归档做大量「急救式修复」：从 `versions/` 回溯旧版本文件、把任意布局里的路径猜回正确位置、按文件名建索引匹配、缺资产定义则拦截。一次架构走查建议新增 `ProjectManager.restore_from_staging()` 独占这些修复，让「导入路径 = 保存路径 = 同套保证」。我们**否决**该建议：这些急救逻辑是导入特有的 I/O，不是 ProjectManager 的领域知识（PM 只操作已安装、规整的项目目录）；导入的契约（使劲修 + 缺定义就拦截）与保存统一入口 `_write_script_unlocked` 的「不更坏」语义（ADR-0002，接受改前就坏的旧剧本）本就不同，强行合一会把两种契约搅在一起，还会往候选 6 要瘦身的 94 方法 PM 上帝模块里再塞约 580 行。

`project_archive` 真正的领域知识泄漏只有「重复的形状常量」：

- **canonical 资源路径**（`resource_type` → 项目内相对路径，如 `characters/{id}.png`、`videos/scene_{id}.mp4`）此前三处各抄一份——`MediaGenerator.OUTPUT_PATTERNS`（写侧）、`versions.py::_resolve_resource_path`（回溯侧）、`project_archive._canonical_resource_path`（导入侧）。收敛为单一函数，三处统一消费。
- **content_mode → 剧本字段名分派**（narration 用 `segments`/`segment_id`、drama 用 `scenes`/`scene_id`）此前 archive 手写 `if/else` 重抄了 `script_models` 已声明的字段。收敛到 `script_models`，archive 调用而非自推导。

`generated_assets` 模板已委托 `PM.create_generated_assets`（非副本），无需处理。

## Consequences

- 与候选 6（拆分 PM 上帝模块）**解耦**：本决策不动 PM，可独立随时落地，不必等 PM 拆分。走查原文说「与候选 6 天然配套」在此反转。
- 保存统一入口仍是剧本写入的单一守卫点（ADR-0002/0003）；导入是**刻意的例外**——它在装入项目目录前先急救脏归档，这层修复发生在统一入口之外。记此 ADR 即为拦住未来「把导入也并进统一入口」的好心改动。
- canonical 路径函数实际跨两个家族：媒体资源（storyboards/videos/grids/reference_videos，归 `MediaGenerator.OUTPUT_PATTERNS`）与角色/场景/道具 sheet（归 `asset_types.bucket_key`）；另有 `characters/refs/{name}.png`（reference_image）不在任何现有 map 内。函数内部分流或先合并这些源，属实现细节。
