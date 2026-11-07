import { defineRegionScenarios } from "../support/scenarios.ts";
import type { ApiOverrides } from "../support/test.ts";

// Light-theme sampling: system light with Follow system preferences renders light immediately.
// Color regressions mostly involve tokens; sample lobby/settings/workspaces/one overlay, with axe contrast covering light too.

const EPISODE_PATH = "/app/projects/demo/episodes/1";
// Project events are unrecorded SSE; reject with nonretryable statuses, retaining recorded project data.
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

defineRegionScenarios("浅色主题", [
  {
    name: "项目大厅",
    path: "/app/projects",
    colorScheme: "light",
    ready: async (page) => {
      await page.getByRole("heading", { level: 1 }).waitFor();
    },
    screenshot: { name: "light-lobby" },
  },
  {
    name: "设置的通用分区",
    path: "/app/settings?section=general",
    colorScheme: "light",
    ready: async (page) => {
      await page.getByRole("combobox", { name: "主题" }).waitFor();
    },
    screenshot: { name: "light-settings-general" },
  },
  {
    name: "工作区的剧集页",
    path: EPISODE_PATH,
    api: EVENT_STREAM,
    colorScheme: "light",
    ready: async (page) => {
      await page.getByRole("button", { name: "添加一集" }).waitFor();
    },
    screenshot: { name: "light-episode" },
  },
  {
    name: "导出范围对话框",
    path: EPISODE_PATH,
    api: EVENT_STREAM,
    colorScheme: "light",
    ready: async (page) => {
      await page.getByRole("button", { name: "添加一集" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: "导出项目归档" }).click();
      await page.getByRole("dialog", { name: "选择导出范围" }).waitFor();
    },
    screenshot: { name: "light-export-dialog" },
  },
]);
