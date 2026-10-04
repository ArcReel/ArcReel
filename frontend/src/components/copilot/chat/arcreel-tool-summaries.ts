import type { SummaryFormat, ToolInput } from "./work-label";

// ---------------------------------------------------------------------------
// ArcReel MCP 工具的一句摘要：键是工具 id（`mcp__arcreel__<id>` 去掉前缀），每个
// 工具都要登记，没有可读参数的工具登记为空摘要。tests/unit/test_frontend_mcp_tool_i18n.py
// 按 server/agent_toolset/toolset.py 的 ARCREEL_MCP_TOOL_IDS 校验这里的键没有缺漏与多余，
// 所以键保持「两格缩进 + 工具 id + 冒号」的写法，一行一个。
//
// 集 ID、剧本文件名与条目 ID 一律换成创作者认得的集名与集内编号（见 SummaryFormat）；
// 条目编号已带集名时不再重复写剧本所属的集。
// 剪辑时间线、生成批次这类不透明 ID 不进摘要。
// ---------------------------------------------------------------------------

type Summarize = (input: ToolInput, f: SummaryFormat) => string;

export const ARCREEL_TOOL_SUMMARIES: Record<string, Summarize> = {
  list_projects: () => "",
  create_project: (i, f) => f.text(i.title) || f.text(i.name),
  upload_source: (i, f) => f.text(i.filename),
  edit_source_text: (i, f) => f.text(i.filename) || f.episode(i.episode_id),
  get_workflow_plan: (i, f) => f.episode(i.episode_id),
  get_video_capabilities: () => "",
  get_prompt_preview: (i, f) => f.item(i.item_id) || f.script(i.script),
  get_generation_batch: () => "",
  cancel_generation_batch: () => "",
  get_project_content: () => "",
  list_source_files: () => "",
  get_source_text: (i, f) => f.path(i.path),
  get_episode_script: (i, f) => f.script(i.script),
  get_script_plan_content: (i, f) => f.episode(i.episode_id),
  list_project_files: () => "",
  read_project_file: (i, f) => f.path(i.path),
  list_pending_assets: (i, f) => f.episode(i.episode_id) || f.assetType(i.type),
  generate_assets: (i, f) => f.list(i.names) || f.episode(i.episode_id) || f.assetType(i.type),
  generate_storyboards: (i, f) => f.items(i.segment_ids) || f.script(i.script),
  edit_images: (i, f) => f.join(f.assetType(i.resource_type), f.items(f.field(i.edits, "id"))),
  generate_narration_audio: (i, f) => f.items(i.segment_ids) || f.script(i.script),
  generate_videos: (i, f) => f.items(f.record(i.target).ids) || f.script(i.script),
  generate_grid: (i, f) => f.items(i.scene_ids) || f.script(i.script),
  split_grids: (i, f) => f.count("tool_summary_grids", i.grid_ids),
  select_video_version: (i, f) => f.join(f.item(i.unit_id), f.version(i.version)),
  inspect_video_units: (i, f) => f.items(i.unit_ids),
  create_timeline: (i, f) => f.join(f.episode(i.episode), f.quote(i.name)),
  list_timelines: (i, f) => f.episode(i.episode),
  read_timeline: () => "",
  edit_timeline: (i, f) => f.text(i.summary),
  rename_timeline: (i, f) => f.quote(i.name),
  list_revisions: () => "",
  restore_revision: (i, f) => f.revision(i.revision),
  list_bgm: () => "",
  render_final_cut: (i, f) => f.revision(i.revision),
  export_jianying_draft: (i, f) => f.revision(i.revision),
  generate_episode_script: (i, f) => f.items(i.entry_ids) || f.episode(i.episode_id),
  generate_script_plan: (i, f) => f.episode(i.episode_id),
  confirm_script_review: (i, f) => f.episode(i.episode_id),
  open_draft: (i, f) => f.episode(i.episode_id),
  patch_draft: (i, f) => f.episode(i.episode_id),
  promote_draft: (i, f) => f.episode(i.episode_id),
  discard_draft: (i, f) => f.episode(i.episode_id),
  patch_project: (i, f) => f.list(Object.keys(f.record(i.entries))) || f.projectFields(i),
  patch_episode_meta: (i, f) => f.join(f.script(i.script), f.quote(i.value)),
  rename_asset: (i, f) => f.arrow(i.old_name, i.new_name),
  merge_asset: (i, f) => f.arrow(i.source, i.target),
  retry_project_migration: () => "",
  patch_episode_script: (i, f) => f.join(f.script(i.script), f.count("tool_summary_operations", i.operations)),
  plan_episodes: () => "",
  reset_episode_planning: (i, f) => f.episode(i.episode_id),
  complete_script_plan_rebuild: (i, f) => f.episode(i.episode_id),
};
