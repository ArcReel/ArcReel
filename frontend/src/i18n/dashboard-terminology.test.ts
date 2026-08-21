import { describe, expect, it } from "vitest";

import enDashboard from "./en/dashboard";
import enWorkflow from "./en/workflow";
import viDashboard from "./vi/dashboard";
import viWorkflow from "./vi/workflow";
import zhDashboard from "./zh/dashboard";
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
