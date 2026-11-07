import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { waitForEntrance } from "../support/region-helpers.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// Header Export project scopes: two choices, editing-view hints, bottom Export.
const EPISODE_PATH = "/app/projects/demo/episodes/1";
// Project events are unrecorded SSE; reject with nonretryable statuses, retaining recorded project data.
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

interface RecordedProject {
  project: { episodes: { episode: number; title?: string }[] };
}

// Long episode-title Open editing view links must wrap rather than push dialogs outside viewports.
const recordedProject = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "project-demo.json"), "utf8")) as RecordedResponse
).body as RecordedProject;
const LONG_EPISODE_TITLE = "第一集：雨夜里的旧城区，主角在废弃车站等待一位从未谋面的委托人，并发现站台广播仍在重复多年前的最后一班列车";
const LONG_EPISODE_NAME: ApiOverrides = {
  ...EVENT_STREAM,
  "GET /api/v1/projects/demo": {
    status: 200,
    body: {
      ...recordedProject,
      project: {
        ...recordedProject.project,
        episodes: recordedProject.project.episodes.map((ep) => ({ ...ep, title: LONG_EPISODE_TITLE })),
      },
    },
  },
};

function exportDialog(page: Page): Locator {
  return page.getByRole("dialog", { name: "选择导出范围" });
}

async function episodeReady(page: Page) {
  await page.getByRole("button", { name: "添加一集" }).waitFor();
}

async function openExportDialog(page: Page) {
  await page.getByRole("button", { name: "导出项目归档" }).click();
  const dialog = exportDialog(page);
  await waitForEntrance(dialog);
  return dialog;
}

defineRegionScenarios("导出范围对话框", [
  {
    name: "打开导出范围对话框，「导出」与两个范围选项都在视口内",
    path: EPISODE_PATH,
    api: EVENT_STREAM,
    ready: episodeReady,
    act: async (page) => {
      const dialog = await openExportDialog(page);
      await expect(dialog.getByRole("radio", { name: /仅当前版本/ })).toBeChecked();
      await expect(dialog.getByRole("radio", { name: /全部数据/ })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "导出", exact: true })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "export-scope-dialog", target: exportDialog },
  },
  {
    name: "集名很长时提示换行，「导出」仍完整可见",
    path: EPISODE_PATH,
    api: LONG_EPISODE_NAME,
    ready: episodeReady,
    act: async (page) => {
      const dialog = await openExportDialog(page);
      await expect(dialog.getByRole("button", { name: /的剪辑视图$/ })).toBeVisible();
      await expect(dialog.getByRole("button", { name: "导出", exact: true })).toBeInViewport({ ratio: 1 });
    },
  },
]);
