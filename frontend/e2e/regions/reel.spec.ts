import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW } from "../support/recorded.ts";
import { test, type ApiOverrides } from "../support/test.ts";

test.use({ locale: "en-US" });

const timelineId = "tl-reel-test";
const base = `/api/v1/projects/demo/edit-timelines/${timelineId}`;
const api: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: {} },
  "GET /api/v1/projects/demo/edit-timelines?episode=1": { status: 200, body: { timelines: [{ id: timelineId, name: "Three minute review cut", episode: 1, revision: 1, clip_count: 45, created_at: FIXED_NOW, updated_at: FIXED_NOW, updated_by: { kind: "creator", user_id: null }, update_summary: "" }] } },
  [`GET ${base}`]: { status: 200, body: {
    timeline: { id: timelineId, name: "Three minute review cut", episode: 1 }, revision: 1, latest_revision: 1, duration: 180, bgm: [], issues: [],
    clips: Array.from({ length: 45 }, (_, index) => ({ id: `c${index}`, unit_id: `E1S${index + 1}`, status: "ready", start: index * 4, duration: 4, video_version: 1, source_duration: 5, trim: null, source_volume: 0, hold: 0, carries_narration: false, narration: null, reason: null, transition_to_next: null })),
  } },
  [`GET ${base}/final-cut?narration=without_narration&subtitles=no_subtitles`]: { status: 200, body: { episode: 1, timeline_id: timelineId, status: "missing", artifact_path: "reel.mp4", version: null, rendered_at: null, narration: "without_narration", subtitles: "no_subtitles", download_url: null } },
};

defineRegionScenarios("Simple reel workspace", [
  { name: "watch and revise with 45 shots", path: "/app/projects/demo/reel/1", api,
    ready: async (page) => { await page.getByRole("button", { name: "Shot 45", exact: true }).waitFor(); },
    act: async (page) => { await page.getByRole("textbox", { name: "What should change?" }).fill("Keep the same dragon proportions and connect its movement into the next shot."); },
  },
  { name: "phone layout", path: "/app/projects/demo/reel/1", api,
    ready: async (page) => { await page.getByRole("button", { name: "Shot 45", exact: true }).waitFor(); },
    act: async (page) => { await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole("textbox", { name: "What should change?" }).scrollIntoViewIfNeeded(); },
  },
]);
