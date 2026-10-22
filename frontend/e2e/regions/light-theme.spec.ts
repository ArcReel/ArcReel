import { defineRegionScenarios } from "../support/scenarios.ts";
import type { ApiOverrides } from "../support/test.ts";

// 浅色主题抽样：系统为浅色、偏好为默认的「跟随系统」时，首屏即以浅色渲染。
// 颜色回归大多出在 token 层，这里只取大厅、设置、工作区与一个弹层；axe 的对比度检查同样覆盖浅色。

const EPISODE_PATH = "/app/projects/demo/episodes/1";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
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
