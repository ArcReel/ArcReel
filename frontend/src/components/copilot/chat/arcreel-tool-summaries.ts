import type { SummaryFormat, ToolInput } from "./work-label";

// ---------------------------------------------------------------------------
// One-sentence ArcReel MCP tool summaries: keys are tool IDs without `mcp__arcreel__<id>` prefixes; every
// tool must register, using empty summaries without readable arguments. tests/unit/test_frontend_mcp_tool_i18n.py
// checks keys exactly against server/agent_toolset/toolset.py ARCREEL_MCP_TOOL_IDS without missing/excess entries,
// so retain two-space indentation + tool ID + colon, one per line.
//
// Replace episode IDs/script filenames/entry IDs with creator-recognizable episode titles/local numbers (see SummaryFormat);
// entry numbers already carrying episode titles never repeat script-owning episodes.
// Opaque editing-timeline/generation-batch IDs never enter summaries.
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
  inspect_images: (i, f) => f.join(f.assetType(i.resource_type), f.items(i.ids), f.version(i.version)),
  get_provider_asset_links: (i, f) => f.join(f.assetType(i.resource_type), f.text(i.resource_id)),
  list_provider_library_assets: () => "",
  upload_to_provider_asset_library: (i, f) => f.join(f.assetType(i.resource_type), f.text(i.resource_id)),
  select_provider_library_asset: (i, f) => f.join(f.assetType(i.resource_type), f.text(i.resource_id)),
  release_provider_asset_link: () => "",
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
  delete_project_file: (i, f) => f.path(i.path),
  list_trash_records: () => "",
  restore_trash_record: () => "",
};
