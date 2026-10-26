import { defineRegionScenarios } from "../support/scenarios.ts";
import { FIXED_NOW, recorded } from "../support/recorded.ts";
import { expect, test, type ApiOverrides } from "../support/test.ts";

test.use({ locale: "en-US" });

const timelineId = "tl-reel-test";
const base = `/api/v1/projects/demo/edit-timelines/${timelineId}`;
const project = recorded<{ project: { episodes: Record<string, unknown>[] } & Record<string, unknown> }>("project-demo.json");
const api: ApiOverrides = {
  "GET /api/v1/files/demo/videos/shot43.mp4": { status: 404, body: { detail: "Media bytes are verified in the live playback check." } },
  "GET /api/v1/projects/demo": { status: 200, body: { ...project,
    project: { ...project.project, episodes: [{ ...project.project.episodes[0], script_file: "scripts/episode_1.json" }] },
    scripts: { "episode_1.json": { episode: 1, title: "Late Again", content_mode: "drama", novel: { title: "", chapter: "" },
      scenes: Array.from({ length: 45 }, (_, index) => ({ scene_id: `E1S${index + 1}`, duration_seconds: 5, segment_break: false, characters_in_scene: [], image_prompt: null, video_prompt: null,
        generated_assets: { video_clip: index % 3 === 0 ? `videos/shot${index + 1}.mp4` : null } })),
    } },
  } },
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: {} },
  "GET /api/v1/projects/demo/edit-timelines?episode=1": { status: 200, body: { timelines: [{ id: timelineId, name: "Three minute review cut", episode: 1, revision: 1, clip_count: 45, created_at: FIXED_NOW, updated_at: FIXED_NOW, updated_by: { kind: "creator", user_id: null }, update_summary: "" }] } },
  [`GET ${base}`]: { status: 200, body: {
    timeline: { id: timelineId, name: "Three minute review cut", episode: 1 }, revision: 1, latest_revision: 1, duration: 180, bgm: [], issues: [],
    clips: Array.from({ length: 45 }, (_, index) => ({ id: `c${index}`, unit_id: `E1S${index + 1}`, status: "ready", start: index * 4, duration: 4, video_version: 1, source_duration: 5, trim: null, source_volume: 0, hold: 0, carries_narration: false, narration: null, reason: null, transition_to_next: null })),
  } },
  [`GET ${base}/final-cut?narration=without_narration&subtitles=no_subtitles`]: { status: 200, body: { episode: 1, timeline_id: timelineId, status: "missing", artifact_path: "reel.mp4", version: null, rendered_at: null, narration: "without_narration", subtitles: "no_subtitles", download_url: null } },
};

defineRegionScenarios("Simple reel workspace", [
  { name: "rendered takes before an assembled export", path: "/app/projects/demo/reel/1", api,
    ready: async (page) => { await page.getByRole("button", { name: "Preview shot 43", exact: true }).waitFor(); },
    act: async (page) => {
      await page.getByRole("button", { name: "Preview shot 43", exact: true }).click();
      await expect(page.getByLabel("Episode screening")).toHaveAttribute("src", /videos\/shot43\.mp4$/);
      await expect(page.getByText("Playing shot 43 · individual take, not the finished cut")).toBeVisible();
      await expect(page.getByRole("button", { name: "Watch stitched cut" })).toBeDisabled();
    },
  },
  { name: "watch and revise with 45 shots", path: "/app/projects/demo/reel/1", api,
    ready: async (page) => { await page.getByRole("button", { name: "Shot 45", exact: true }).waitFor(); },
    act: async (page) => { await page.getByRole("textbox", { name: "What should change?" }).fill("Keep the same dragon proportions and connect its movement into the next shot."); },
  },
  { name: "phone layout", path: "/app/projects/demo/reel/1", api,
    ready: async (page) => { await page.getByRole("button", { name: "Shot 45", exact: true }).waitFor(); },
    act: async (page) => { await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole("textbox", { name: "What should change?" }).scrollIntoViewIfNeeded(); },
  },
]);
