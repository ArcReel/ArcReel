import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 「供应商」分区：全出血档的二级栏（预置 | 自定义 Tab、紧凑档图标栏）与预置供应商详情（密钥、高级配置、底部保存栏）。

const PROVIDERS_PATH = "/app/settings?section=providers";

interface RecordedProvider {
  id: string;
  credential_count: number;
}

// 录制环境没有配置任何密钥：在录制的目录上给几个预置供应商补上密钥数量，二级栏第二行才有「已配置 N 个密钥」。
const recordedProviders = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "providers.json"), "utf8")) as RecordedResponse
).body as { providers: RecordedProvider[] };

const PROVIDERS_WITH_KEYS: ApiOverrides = {
  "GET /api/v1/providers": {
    status: 200,
    body: {
      providers: recordedProviders.providers.map((provider, index) => ({
        ...provider,
        credential_count: provider.id === "gemini-aistudio" ? 12 : index % 3 === 0 ? index + 1 : 0,
      })),
    },
  },
};

// 与上面的密钥数量一致：AI Studio 有生效密钥，详情页头显示「已就绪」。
const recordedAiStudioConfig = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "provider-gemini-aistudio-config.json"), "utf8")) as RecordedResponse
).body as Record<string, unknown>;

// 压力变体：密钥多、名称与接口地址长，详情正文必须在自己的栏里滚动，底部保存栏始终可见。
const MANY_CREDENTIALS: ApiOverrides = {
  "GET /api/v1/providers/gemini-aistudio/config": {
    status: 200,
    body: { ...recordedAiStudioConfig, status: "ready" },
  },
  "GET /api/v1/providers/gemini-aistudio/credentials": {
    status: 200,
    body: {
      credentials: Array.from({ length: 12 }, (_, i) => ({
        id: i + 1,
        provider: "gemini-aistudio",
        name: `团队共享账号 ${i + 1} · 华东二区备用线路（按量计费，月底结算）`,
        api_key_masked: "AIza…x9Qk",
        credentials_filename: null,
        base_url: `https://generativelanguage-proxy-${i + 1}.internal.example.com/v1beta/very/long/path`,
        is_active: i === 0,
        created_at: "2026-01-01T08:00:00.000Z",
      })),
    },
  },
};

// 压力变体：自定义供应商多且名称长，「自定义」Tab 下的列表在自己的栏里滚动到底。
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

const rail = (page: Page) => page.getByRole("navigation", { name: "供应商列表" });

async function presetDetailReady(page: Page) {
  await page.getByRole("heading", { name: "AI Studio" }).waitFor();
  await page.getByRole("button", { name: "添加密钥" }).waitFor();
}

defineRegionScenarios("供应商", [
  {
    name: "预置供应商详情：密钥多时正文在详情栏里滚动，底部保存栏始终可见",
    path: PROVIDERS_PATH,
    api: { ...PROVIDERS_WITH_KEYS, ...MANY_CREDENTIALS },
    ready: presetDetailReady,
    act: async (page) => {
      const save = page.getByRole("button", { name: "保存" });
      await expect(save).toBeDisabled();
      const firstField = page.getByRole("main").getByRole("spinbutton").first();
      await firstField.scrollIntoViewIfNeeded();
      await firstField.fill("4");
      await expect(save).toBeInViewport({ ratio: 1 });
      await expect(save).toBeEnabled();
    },
    screenshot: { name: "providers-preset-detail", target: (page) => page.getByRole("main") },
  },
  {
    name: "自定义供应商多且名称长：切到「自定义」后第一个位于栏顶，列表滚动到底",
    path: PROVIDERS_PATH,
    api: { ...PROVIDERS_WITH_KEYS, ...MANY_CUSTOM_PROVIDERS },
    ready: presetDetailReady,
    act: async (page) => {
      const customTab = rail(page).getByRole("tab", { name: /自定义/ });
      // 紧凑档是图标栏，两组上下叠放、没有 Tab
      if (await customTab.isVisible()) {
        await customTab.click();
        const first = rail(page).getByRole("tabpanel").getByRole("link").first();
        await expect(first).toContainText("网关 1 ·");
        const [railBox, firstBox, tabBox] = await Promise.all([
          rail(page).boundingBox(),
          first.boundingBox(),
          customTab.boundingBox(),
        ]);
        // 第一个自定义供应商紧接在 Tab 下面，而不是排在预置供应商之后
        expect(firstBox!.y - railBox!.y).toBeLessThan(tabBox!.y - railBox!.y + tabBox!.height + 24);
      }
      await rail(page).evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(rail(page).getByRole("link", { name: /添加自定义供应商/ }).filter({ visible: true })).toBeInViewport();
    },
    screenshot: { name: "providers-rail-custom", target: rail },
  },
  {
    name: "打开「添加密钥」对话框，标题与操作按钮留在视口内",
    path: PROVIDERS_PATH,
    ready: presetDetailReady,
    act: async (page) => {
      await page.getByRole("button", { name: "添加密钥" }).click();
      const dialog = page.getByRole("dialog", { name: "添加密钥" });
      await expect(dialog).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "添加密钥" })).toBeInViewport({ ratio: 1 });
      // 等进场过渡结束再探测：淡入途中的半透明文字会被 axe 判为对比度不足
      await dialog.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
    },
    screenshot: { name: "providers-add-key-dialog", target: (page) => page.getByRole("dialog") },
  },
]);
