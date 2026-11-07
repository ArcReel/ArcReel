import type { Page } from "@playwright/test";
import { clearAgentOverlay, waitForEntrance } from "../support/region-helpers.ts";
import { recorded } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Storyboard video ending-frame pickers: flat episode storyboard/grid-crop groups; select images as ending frames.
const BOARD_PATH = "/app/projects/demo/episodes/1";

interface RecordedProject {
  project: Record<string, unknown>;
  scripts: Record<string, Record<string, unknown>>;
}

const project = recorded<RecordedProject>("project-demo.json");
const recordedScript = project.scripts["episode_1.json"];

const shotId = (n: number) => `E1S${String(n).padStart(2, "0")}`;

// Storyboard/crop images use inline SVG: getFileUrl preserves data: URLs without requests for stable screenshots.
function portraitSvg(hue: number) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640" viewBox="0 0 90 160"><rect width="90" height="160" fill="hsl(${hue} 30% 22%)"/><circle cx="45" cy="62" r="20" fill="hsl(${hue} 35% 55%)"/><rect x="18" y="96" width="54" height="48" rx="6" fill="hsl(${hue} 30% 40%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

const SHOT_COUNT = 24;
const GRID_CELLS = 9;

// All 24 storyboards have images, plus 9 crops from a nine-grid; dialog bodies scroll internally while footer buttons remain.
const scenes = Array.from({ length: SHOT_COUNT }, (_, i) => ({
  scene_id: shotId(i + 1),
  duration_seconds: 4,
  segment_break: false,
  characters_in_scene: [],
  scenes: [],
  props: [],
  image_prompt: `${shotId(i + 1)}：雨夜旧城的街景。`,
  video_prompt: `${shotId(i + 1)}：镜头跟随人物平移。`,
  utterances: [],
  source_text: `${shotId(i + 1)} 对应的原文。`,
  generated_assets: {
    storyboard_image: portraitSvg((i * 37) % 360),
    storyboard_last_image: null,
    grid_id: null,
    grid_cell_index: null,
    video_clip: null,
    video_thumbnail: null,
    video_uri: null,
    status: "storyboard_ready",
  },
}));

const API: ApiOverrides = {
  // Project event SSE streams are unrecorded; reject with nonretryable statuses so pages retain substituted data.
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...project,
      project: { ...project.project, content_mode: "drama" },
      scripts: { "episode_1.json": { ...recordedScript, content_mode: "drama", segments: undefined, scenes } },
    },
  },
  "GET /api/v1/projects/demo/grids": {
    status: 200,
    body: [
      {
        id: "g1",
        episode: 1,
        script_file: "episode_1.json",
        scene_ids: scenes.slice(0, GRID_CELLS).map((scene) => scene.scene_id),
        grid_image_path: portraitSvg(0),
        rows: 3,
        cols: 3,
        cell_count: GRID_CELLS,
        frame_chain: Array.from({ length: GRID_CELLS }, (_, index) => ({
          index,
          row: Math.floor(index / 3),
          col: index % 3,
          frame_type: "transition",
          prev_scene_id: null,
          next_scene_id: null,
          image_path: portraitSvg((index * 53 + 20) % 360),
        })),
        status: "completed",
        prompt: null,
        provider: "gemini",
        model: "nano-banana",
        grid_size: "grid_9",
        created_at: "2026-01-01T00:00:00Z",
        error_message: null,
        split_at: "2026-01-01T00:10:00Z",
      },
    ],
  },
};

const picker = (page: Page) => page.getByRole("dialog", { name: "选择尾帧" });

defineRegionScenarios("尾帧选图器", [
  {
    name: "分镜图与切图很多时打开尾帧选图器，选中一张后底部按钮可用",
    path: BOARD_PATH,
    api: API,
    ready: async (page) => {
      await page.getByRole("navigation", { name: "分镜列表" }).waitFor();
      await page.getByRole("heading", { name: "引用" }).waitFor();
    },
    act: async (page) => {
      await clearAgentOverlay(page);
      const toggle = page.getByRole("button", { name: /^尾帧/ });
      await toggle.scrollIntoViewIfNeeded();
      await toggle.click();
      await page.getByRole("button", { name: "选择图片" }).click();

      const dialog = picker(page);
      const lastCell = dialog.getByRole("button", { name: `grid_9 第 ${GRID_CELLS} 格` });
      await lastCell.scrollIntoViewIfNeeded();
      await lastCell.click();
      await expect(lastCell).toHaveAttribute("aria-pressed", "true");
      await expect(dialog.getByRole("button", { name: "设为尾帧" })).toBeEnabled();
      await expect(dialog.getByRole("button", { name: "设为尾帧" })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "上传" })).toBeInViewport({ ratio: 1 });
      await waitForEntrance(dialog);
    },
    screenshot: { name: "end-frame-picker", target: picker },
  },
]);
