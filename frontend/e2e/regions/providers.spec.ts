import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 「供应商」分区：全出血档的二级栏（预置 | 自定义 Tab、紧凑档图标栏）、预置供应商详情（密钥、高级配置、底部保存栏）
// 与自定义供应商详情（连接、模型表格、并发上限、底部保存栏）。

const PROVIDERS_PATH = "/app/settings?section=providers";

interface RecordedProvider {
  id: string;
  credential_count: number;
  status: "ready" | "unconfigured";
}

// 录制环境没有配置任何密钥：在录制的目录上给几个预置供应商补上密钥数量，二级栏第二行才有「已配置 N 个密钥」。
const recordedProviders = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "providers.json"), "utf8")) as RecordedResponse
).body as { providers: RecordedProvider[] };

const PROVIDERS_WITH_KEYS: ApiOverrides = {
  "GET /api/v1/providers": {
    status: 200,
    body: {
      providers: recordedProviders.providers.map((provider, index) => {
        const credentialCount = provider.id === "gemini-aistudio" ? 12 : index % 3 === 0 ? index + 1 : 0;
        return {
          ...provider,
          credential_count: credentialCount,
          status: credentialCount > 0 ? "ready" : "unconfigured",
        };
      }),
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

// 压力变体：一个自定义供应商挂 9 个模型，名称、接口地址与模型 ID 都长；多于 5 个模型时出现搜索与类型筛选。
const CUSTOM_PROVIDER_ID = 7;
const CUSTOM_PROVIDER_PATH = `${PROVIDERS_PATH}&custom=${CUSTOM_PROVIDER_ID}`;
const CUSTOM_PROVIDER_NAME = "自建 OpenAI 兼容网关 · 华东二区备用线路（按量计费，月底结算）";

const VIDEO_CAPABILITIES = {
  first_frame: true,
  last_frame: true,
  max_reference_images: 0,
  reference_audio_mode: "none",
  max_reference_audio_count: 0,
};

const customModel = (id: number, model_id: string, endpoint: string, extra: Record<string, unknown> = {}) => ({
  id,
  model_id,
  display_name: model_id,
  endpoint,
  is_default: false,
  is_enabled: true,
  price_unit: null,
  price_input: null,
  price_output: null,
  currency: null,
  supported_durations: null,
  resolution: null,
  max_output_tokens: null,
  system_capabilities: null,
  capability_overrides: null,
  global_bucket_refs: null,
  ...extra,
});

const customProvider = {
  id: CUSTOM_PROVIDER_ID,
  display_name: CUSTOM_PROVIDER_NAME,
  discovery_format: "openai",
  base_url: "https://gateway-east-2.internal.example.com/openai-compatible/v1/very/long/path/for/regional/failover",
  api_key_masked: "sk-****",
  models: [
    customModel(1, "deepseek-ai/DeepSeek-V3.2-Exp-Thinking-128k-2026-09-preview", "openai-chat", {
      is_default: true,
      price_unit: "token",
      price_input: 2,
      price_output: 8,
      currency: "CNY",
      max_output_tokens: 32768,
    }),
    customModel(2, "qwen3-max-preview", "openai-chat", { is_enabled: false }),
    customModel(3, "gemini-3-pro-image-preview", "gemini-image", { is_default: true, resolution: "2K" }),
    customModel(4, "gpt-image-1", "openai-images", { price_unit: "image", price_input: 0.04, currency: "USD" }),
    customModel(5, "kling-v2-6-master-image-to-video-pro-1080p", "kling-video", {
      is_default: true,
      supported_durations: [5, 10],
      resolution: "1080p",
      system_capabilities: VIDEO_CAPABILITIES,
      capability_overrides: { last_frame: false },
    }),
    customModel(6, "doubao-seedance-1-0-pro-250528", "ark-seedance", {
      supported_durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
      system_capabilities: VIDEO_CAPABILITIES,
      global_bucket_refs: ["default_video_backend_i2v"],
    }),
    customModel(7, "MiniMax-Hailuo-02", "minimax-hailuo-v1", { system_capabilities: VIDEO_CAPABILITIES }),
    customModel(8, "tts-1-hd", "openai-tts", { is_default: true }),
    customModel(9, "wan2.5-i2v-preview", "dashscope-async-video", { is_enabled: false }),
  ],
  created_at: "2026-09-01T00:00:00Z",
  image_max_workers: 4,
  video_max_workers: null,
  audio_max_workers: null,
};

const CUSTOM_PROVIDER_DETAIL: ApiOverrides = {
  "GET /api/v1/custom-providers": { status: 200, body: { providers: [customProvider] } },
  [`GET /api/v1/custom-providers/${CUSTOM_PROVIDER_ID}`]: { status: 200, body: customProvider },
};

async function customDetailReady(page: Page) {
  await page.getByRole("heading", { name: CUSTOM_PROVIDER_NAME }).waitFor();
  await page.getByRole("table").waitFor();
}

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
    },
    screenshot: { name: "providers-add-key-dialog", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "自定义供应商详情：六列完整，展开两行后正文在详情栏里滚动，底部保存栏始终可见",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const main = page.getByRole("main");
      await expect(main.getByRole("searchbox", { name: "搜索模型…" })).toBeVisible();
      await expect(main.getByRole("group", { name: "按类型筛选" })).toBeVisible();
      const viewport = page.viewportSize()!;
      if (viewport.width >= 1440) {
        // 「启用」列的表头是全选框
        await expect(main.getByRole("checkbox", { name: "启用列表中的全部模型" })).toBeInViewport({ ratio: 1 });
        for (const column of ["模型 ID", "类型", "调用端点", "价格", "默认"]) {
          await expect(main.getByRole("columnheader", { name: column, exact: true })).toBeInViewport({ ratio: 1 });
        }
      }

      await main.getByRole("button", { name: /^编辑 deepseek-ai/ }).click();
      await main.getByRole("button", { name: /^编辑 kling-v2-6/ }).click();
      const modelIds = main.getByRole("textbox", { name: "模型 ID" });
      await expect(modelIds).toHaveCount(2);
      if (viewport.width >= 1440) {
        // 长模型 ID 在输入框里完整可见，不需要横向滚动才能编辑
        const first = modelIds.first();
        const fits = await first.evaluate((el: HTMLInputElement) => el.scrollWidth <= el.clientWidth);
        expect(fits).toBe(true);
      }

      await modelIds.first().fill("deepseek-ai/DeepSeek-V3.2-Exp");
      const save = page.getByRole("button", { name: "保存" });
      await expect(save).toBeInViewport({ ratio: 1 });
      await expect(save).toBeEnabled();
    },
    screenshot: { name: "providers-custom-detail", target: (page) => page.getByRole("main") },
  },
  {
    name: "自定义供应商详情：打开调用端点下拉，按媒体分组的选项留在视口内",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const main = page.getByRole("main");
      await main.getByRole("button", { name: /^编辑 gpt-image-1/ }).click();
      await main.getByRole("combobox", { name: "调用端点" }).click();
      const listbox = page.getByRole("listbox");
      await expect(listbox.getByRole("option", { name: /OpenAI 图片/ }).first()).toBeVisible();
      await expect(listbox).toBeInViewport();
    },
    // 下拉渲染在弹层里，截详情栏连同盖在上面的下拉一起看
    screenshot: { name: "providers-custom-endpoint-select", target: (page) => page.getByRole("main") },
  },
  {
    name: "自定义供应商详情：删除确认写明模型数量，操作按钮留在视口内",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      await page.getByRole("main").getByRole("button", { name: "更多操作" }).click();
      await page.getByRole("menuitem", { name: "删除供应商" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog).toContainText("9 个模型");
      await expect(dialog.getByRole("button", { name: "删除供应商" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "providers-custom-delete-dialog", target: (page) => page.getByRole("alertdialog") },
  },
]);
