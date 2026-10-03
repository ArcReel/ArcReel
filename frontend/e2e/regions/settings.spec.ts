import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 页面外壳与全局设置导航：三档容器（全出血、限宽、铺满）、外壳保存栏与侧栏。
// 各分区的内容由后续重做，这里只截外壳自己的区域（侧栏、保存栏、「通用」分区）。

function settings(section: string) {
  return `/app/settings?section=${section}`;
}

// 压力变体：自定义供应商多且名称长，供应商列表必须在自己的栏里滚动。
const MANY_CUSTOM_PROVIDERS: ApiOverrides = {
  "GET /api/v1/custom-providers": {
    status: 200,
    body: {
      providers: Array.from({ length: 24 }, (_, i) => ({
        id: i + 1,
        display_name: `自建 OpenAI 兼容网关 ${i + 1} · 华东二区备用线路（按量计费）`,
        discovery_format: "openai",
        base_url: `https://gateway-${i + 1}.example.com/v1`,
        api_key_masked: "sk-****",
        models: [],
        created_at: "2026-09-01T00:00:00Z",
        image_max_workers: null,
        video_max_workers: null,
        audio_max_workers: null,
      })),
    },
  },
};

async function settingsReady(page: Page) {
  await page.getByRole("navigation", { name: "设置" }).getByRole("link", { name: "关于" }).waitFor();
}

defineRegionScenarios("全局设置", [
  {
    name: "默认落在全出血档的供应商，侧栏与供应商列表各自滚动",
    path: "/app/settings",
    ready: async (page) => {
      await settingsReady(page);
      await expect(page.getByRole("link", { name: "供应商" })).toHaveAttribute("aria-current", "page");
    },
    screenshot: { name: "settings-sidebar", target: (page) => page.getByRole("navigation", { name: "设置" }) },
  },
  {
    name: "自定义供应商多且名称长时，供应商列表在自己的栏里滚动到底",
    path: "/app/settings",
    api: MANY_CUSTOM_PROVIDERS,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("button", { name: /华东二区备用线路/ }).first().waitFor();
    },
    act: async (page) => {
      const list = page.getByRole("navigation", { name: "供应商列表" });
      await list.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(list.getByRole("button", { name: /网关 24 ·/ })).toBeInViewport();
    },
  },
  {
    name: "限宽档的保存栏固定在外壳底行，表单滚动时保存按钮始终可见",
    path: settings("default-models"),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("textbox", { name: "视频轮询超时（秒）" }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await expect(main.getByRole("button", { name: "保存" })).toHaveCount(0);
      await page.getByRole("textbox", { name: "视频轮询超时（秒）" }).fill("7200");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("button", { name: "保存" })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
    },
  },
  {
    name: "通用分区没有保存栏，外壳不显示底行",
    path: settings("general"),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("combobox", { name: "界面语言" }).waitFor();
    },
    act: async (page) => {
      await expect(page.getByRole("button", { name: "保存" })).toHaveCount(0);
    },
    screenshot: { name: "settings-general", target: (page) => page.getByRole("main") },
  },
  {
    name: "打开界面语言下拉，选项留在视口内",
    path: settings("general"),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("combobox", { name: "界面语言" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("combobox", { name: "界面语言" }).click();
      const listbox = page.getByRole("listbox");
      await expect(listbox).toBeInViewport({ ratio: 1 });
      await expect(listbox.getByRole("option", { name: "Tiếng Việt" })).toBeVisible();
    },
  },
]);
