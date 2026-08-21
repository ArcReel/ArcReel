import { describe, expect, it } from "vitest";

import enDashboard from "./en/dashboard";
import enWorkflow from "./en/workflow";
import viDashboard from "./vi/dashboard";
import viWorkflow from "./vi/workflow";
import zhDashboard from "./zh/dashboard";
import zhEvents from "./zh/events";
import zhOnboarding from "./zh/onboarding";
import zhTemplates from "./zh/templates";
import zhWorkflow from "./zh/workflow";

const narrationAudioDashboardKeys = [
  "default_audio_model",
  "task_type_tts",
  "narration_task_submitted_toast",
  "narration_batch_submitted_toast",
  "narration_batch_none_missing_toast",
  "generate_narration_failed",
  "version_audio_preview_label",
  "batch_generate_narration",
  "tool_name_generate_narration_audio",
  "skill_name_generate_narration_audio",
  "media_narration_title",
  "media_generate_narration",
  "media_regenerate_narration",
  "narration_audio_player_label",
] as const;

describe.each([
  { locale: "zh", term: "旁白配音", dashboard: zhDashboard, workflow: zhWorkflow },
  { locale: "en", term: "narration audio", dashboard: enDashboard, workflow: enWorkflow },
  { locale: "vi", term: "âm thanh thuyết minh", dashboard: viDashboard, workflow: viWorkflow },
])("$locale narration-audio terminology", ({ term, dashboard, workflow }) => {
  it("names the audio artifact throughout its UI lifecycle", () => {
    for (const key of narrationAudioDashboardKeys) {
      expect(dashboard[key].toLocaleLowerCase()).toContain(term);
    }
    expect(workflow.task_type_tts.toLocaleLowerCase()).toContain(term);
  });
});

const storyboardEntryDashboardKeys = [
  "content_mode_ad_desc",
  "duration_locked_generating",
  "shot_label",
  "review_shots_label",
  "review_shot_text_placeholder",
  "detail_voiceover_placeholder",
  "shot_move_earlier",
  "shot_move_later",
  "reorder_shot_failed",
  "insufficient_scenes_for_grid",
  "end_frame_unsupported_notice",
  "end_frame_busy_hint",
  "end_frame_preview_alt",
  "end_frame_set_success",
  "end_frame_clear_success",
  "end_frame_picker_storyboard_label",
] as const;

it("uses 分镜 for script entries and reserves 镜头 for cinematography", () => {
  for (const key of storyboardEntryDashboardKeys) {
    expect(zhDashboard[key]).toContain("分镜");
    expect(zhDashboard[key]).not.toContain("镜头");
  }

  for (const key of [
    "label.skeleton_segments",
    "label.skeleton_scenes",
    "label.skeleton_shots",
    "entity.segment",
    "entity.drama_scene",
    "entity.shot",
  ] as const) {
    expect(zhEvents[key]).toContain("分镜");
    expect(zhEvents[key]).not.toContain("镜头");
  }

  expect(zhOnboarding.workbench_timeline_body).toContain("分镜");
  expect(zhOnboarding.workbench_timeline_body).not.toContain("镜头");
  expect(zhTemplates.bucket_r2v_caption).toContain("视频单元");
  expect(zhTemplates.bucket_r2v_caption).not.toContain("镜头");

  expect(zhDashboard.camera_motion_label).toContain("镜头");
  expect(zhDashboard.shot_type_over_the_shoulder).toContain("镜头");
});
