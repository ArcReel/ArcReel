import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 「供应商」分区：全出血档的二级栏（预置 | 自定义 Tab、紧凑档图标栏）、预置供应商详情（密钥、高级配置、底部保存栏）
// 与自定义供应商详情（连接、模型列表、并发上限、底部保存栏），以及点开模型行打开的模型 Sheet。

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

// 压力变体：模型列表里有已下架、未定价的模型与很长的模型 ID，状态标记与价格摘要换行而不撑出横向滚动。
const recordedAiStudioModels = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "provider-gemini-aistudio-models.json"), "utf8")) as RecordedResponse
).body as { models: Record<string, unknown>[] };

const MODEL_LIST_STATES: ApiOverrides = {
  "GET /api/v1/providers/gemini-aistudio/models": {
    status: 200,
    body: {
      discoverable: true,
      models: [
        ...recordedAiStudioModels.models,
        {
          model_id: "gemini-2.0-flash-experimental-image-generation-legacy-2025-03-preview",
          api_model_name: "gemini-2.0-flash-experimental-image-generation-legacy-2025-03-preview",
          display_name: "Gemini 2.0 Flash 图片生成（实验版，已停止维护的旧接口）",
          media_type: "image",
          enabled: false,
          is_default: false,
          official: true,
          flags: ["discontinued", "unpriced"],
          price: null,
          endpoint: null,
        },
      ],
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

// 压力变体：一个自定义供应商挂 9 个模型，名称、接口地址与模型 ID 都长。
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

const entry = (maker: string, name: string, media_type: string, display_name: string, priced = true) => ({
  maker,
  name,
  media_type,
  display_name,
  priced,
});

const customModel = (id: number, model_id: string, endpoint: string, extra: Record<string, unknown> = {}) => ({
  id,
  model_id,
  display_name: model_id,
  endpoint,
  is_default: false,
  is_enabled: true,
  resolution: null,
  multiplier: 1,
  entry_binding: null,
  matched_entry: null,
  system_capabilities: null,
  capability_overrides: null,
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
      multiplier: 0.8,
      matched_entry: entry("deepseek", "deepseek-v3.2-exp", "text", "DeepSeek V3.2 Exp"),
      capability_overrides: { max_output_tokens: 32768 },
    }),
    customModel(2, "qwen3-max-preview", "openai-chat", { is_enabled: false }),
    customModel(3, "gemini-3-pro-image-preview", "gemini-image", {
      is_default: true,
      resolution: "2K",
      matched_entry: entry("google", "gemini-3-pro-image-preview", "image", "Gemini 3 Pro Image Preview"),
    }),
    customModel(4, "gpt-image-1", "openai-images", {
      matched_entry: entry("openai", "gpt-image-1", "image", "GPT Image 1", false),
    }),
    customModel(5, "kling-v2-6-master-image-to-video-pro-1080p", "kling-video", {
      is_default: true,
      resolution: "1080p",
      multiplier: 1.25,
      matched_entry: entry("kling", "kling-v2-6-master", "video", "Kling v2.6 Master"),
      system_capabilities: VIDEO_CAPABILITIES,
      capability_overrides: { last_frame: false, durations: [5, 10] },
    }),
    customModel(6, "doubao-seedance-1-0-pro-250528", "ark-seedance", {
      matched_entry: entry("bytedance", "doubao-seedance-1-0-pro", "video", "Doubao Seedance 1.0 Pro"),
      system_capabilities: VIDEO_CAPABILITIES,
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

const ENDPOINT_MEDIA: Record<string, string> = {
  "openai-chat": "text",
  "gemini-image": "image",
  "openai-images": "image",
  "kling-video": "video",
  "ark-seedance": "video",
  "minimax-hailuo-v1": "video",
  "openai-tts": "audio",
  "dashscope-async-video": "video",
};

// 模型列表的行（GET /providers/custom-7/models）与已保存的模型一一对应
const customListItems = customProvider.models.map((m) => ({
  model_id: m.model_id,
  api_model_name: m.model_id,
  display_name: null,
  media_type: ENDPOINT_MEDIA[m.endpoint],
  enabled: m.is_enabled,
  is_default: m.is_default,
  official: false,
  flags: m.matched_entry && (m.matched_entry as { priced: boolean }).priced ? [] : ["unpriced"],
  price:
    m.endpoint === "kling-video"
      ? { currency: "CNY", items: [{ meter: "output_seconds", min_price: 0.5, max_price: 1, per: 1 }] }
      : null,
  endpoint: m.endpoint,
}));

const KLING_ID = "kling-v2-6-master-image-to-video-pro-1080p";

const KLING_FACTS = {
  durations: [5, 10],
  resolutions: ["720p", "1080p"],
  duration_constraints: { by_resolution: { "1080p": [5] }, with_reference_images: [5] },
};

const KLING_OFFICIAL_PRICE = {
  currency: "CNY",
  meters: [
    {
      meter: "output_seconds",
      dims: ["resolution", "audio"],
      rows: [
        { when: { resolution: "720p", audio: false }, price: 0.6 },
        { when: { resolution: "720p", audio: true }, price: 0.8 },
        { when: { resolution: "1080p", audio: false }, price: 0.8 },
        { when: { resolution: "1080p", audio: true }, price: 1 },
      ],
      defaults: { resolution: "720p", audio: false },
    },
    { meter: "input_images", dims: [], rows: [{ when: {}, price: 0.05 }], free_allowance: 2 },
  ],
};

// 模型 Sheet 的压力内容：视频模型的参数全部可改，其中两项已修改；下段的模型信息用户改过，
// 各字段下方带官方值，价格是分辨率 × 有声的四档加带免费额度的参考图，另有两个供应商共用这个条目
const klingDetail = {
  model: customListItems.find((m) => m.model_id === KLING_ID),
  multiplier: 1.25,
  provider_multiplier: 0.9,
  entry: {
    maker: "kling",
    name: "kling-v2-6-master",
    display_name: "Kling v2.6 Master（中转站自用，最长 15 秒）",
    media_type: "video",
    aliases: ["kling-v2.6-master", "kling-v2-6-master-image-to-video"],
    discontinued: false,
    context_length: null,
    facts: { ...KLING_FACTS, durations: [5, 10, 15], resolutions: ["720p", "1080p", "2160p"] },
    price: {
      ...KLING_OFFICIAL_PRICE,
      meters: [
        {
          ...KLING_OFFICIAL_PRICE.meters[0],
          rows: KLING_OFFICIAL_PRICE.meters[0].rows.map((row) => ({ ...row, price: row.price + 0.1 })),
        },
        KLING_OFFICIAL_PRICE.meters[1],
      ],
    },
    source: "modified",
    official: { display_name: "Kling v2.6 Master", facts: KLING_FACTS, price: KLING_OFFICIAL_PRICE },
    shared_with: [
      { provider_id: "kling", display_name: "可灵" },
      { provider_id: "custom-8", display_name: "海外备用中转站（按量计费，晚高峰限流）" },
    ],
  },
  entry_binding: "kling/kling-v2-6-master-image-to-video-pro-1080p-with-a-long-binding-name",
  capabilities: {
    media_type: "video",
    editable: [
      "durations",
      "duration_constraints",
      "resolutions",
      "text_to_video",
      "first_frame",
      "last_frame",
      "max_reference_images",
      "reference_audio_mode",
      "max_reference_audio_count",
      "max_reference_audio_total_seconds",
      "max_prompt_chars",
      "audio_track",
      "reference_route_audio_track",
    ],
    base: {
      durations: [5, 10],
      duration_constraints: { by_resolution: { "1080p": [5] }, with_reference_images: [5] },
      resolutions: ["720p", "1080p"],
      text_to_video: true,
      first_frame: true,
      last_frame: true,
      max_reference_images: 4,
      reference_audio_mode: "none",
      max_reference_audio_count: 0,
      max_reference_audio_total_seconds: null,
      max_prompt_chars: 2500,
      audio_track: "controllable",
      reference_route_audio_track: "always_off",
    },
    overrides: { last_frame: false, durations: [5, 10] },
    effective: {},
  },
  endpoint: "kling-video",
  resolution: "1080p",
  path_overrides: null,
  requests: [],
};

const gptImageDetail = {
  model: customListItems.find((m) => m.model_id === "gpt-image-1"),
  multiplier: 1,
  provider_multiplier: 0.9,
  entry: null,
  entry_binding: null,
  capabilities: {
    media_type: "image",
    editable: ["text_to_image", "image_to_image", "resolutions"],
    base: { text_to_image: true, image_to_image: true, resolutions: null },
    overrides: {},
    effective: {},
  },
  endpoint: "openai-images",
  resolution: null,
  path_overrides: null,
  requests: [],
};

const CUSTOM_PROVIDER_DETAIL: ApiOverrides = {
  "GET /api/v1/custom-providers": { status: 200, body: { providers: [customProvider] } },
  [`GET /api/v1/custom-providers/${CUSTOM_PROVIDER_ID}`]: { status: 200, body: customProvider },
  [`GET /api/v1/providers/custom-${CUSTOM_PROVIDER_ID}/multiplier`]: { status: 200, body: { multiplier: 0.9, default: 1 } },
  [`GET /api/v1/providers/custom-${CUSTOM_PROVIDER_ID}/models`]: {
    status: 200,
    body: { discoverable: true, models: customListItems },
  },
  [`GET /api/v1/providers/custom-${CUSTOM_PROVIDER_ID}/models/${KLING_ID}`]: { status: 200, body: klingDetail },
  [`GET /api/v1/providers/custom-${CUSTOM_PROVIDER_ID}/models/gpt-image-1`]: { status: 200, body: gptImageDetail },
};

// 压力变体：「获取模型」两组都有、模型 ID 很长；已添加的 gpt-image-1 不列出。
const fetched = (model_id: string, media_type: string, recognized: boolean, matched_entry: unknown = null) => ({
  model_id,
  display_name: model_id,
  media_type,
  recognized,
  model_key: null,
  matched_entry,
  endpoint: media_type === "video" ? "openai-video" : media_type === "image" ? "openai-images" : "openai-chat",
  endpoint_by_media_type: {
    text: "openai-chat",
    image: "openai-images",
    video: "openai-video",
    audio: "openai-tts",
  },
  is_default: false,
});

const CUSTOM_DISCOVERY: ApiOverrides = {
  [`POST /api/v1/providers/custom-${CUSTOM_PROVIDER_ID}/models/discover`]: {
    status: 200,
    body: {
      not_applicable: false,
      reason: null,
      models: [
        fetched("gpt-image-1", "image", true, entry("openai", "gpt-image-1", "image", "GPT Image 1")),
        fetched("gpt-5.5", "text", true, entry("openai", "gpt-5.5", "text", "GPT-5.5")),
        fetched(
          "doubao-seedance-2-0-pro-260128-relay-cn-east-2-high-priority",
          "video",
          true,
          entry("bytedance", "doubao-seedance-2-0-pro-260128", "video", "Seedance 2.0 Pro"),
        ),
        fetched("house-chat-internal-finetune-2026-09-customer-support-zh", "text", false),
        fetched("acme-video-pro", "video", false),
      ],
    },
  },
};

const SEEDANCE_ENTRIES = [
  { maker: "bytedance", name: "doubao-seedance-2-0-pro-260128", media_type: "video", display_name: "Seedance 2.0 Pro", discontinued: false },
  {
    maker: "bytedance",
    name: "doubao-seedance-1-0-lite-i2v-250428",
    media_type: "video",
    display_name: "Seedance 1.0 Lite 图生视频（旧版）",
    discontinued: true,
  },
];

async function customDetailReady(page: Page) {
  await page.getByRole("heading", { name: CUSTOM_PROVIDER_NAME }).waitFor();
  await page.getByRole("region", { name: "模型" }).getByRole("listitem").first().waitFor();
}

/** 点开模型列表里的一行，等模型 Sheet 读完。 */
async function openModelSheet(page: Page, modelId: string) {
  const row = page.getByRole("region", { name: "模型" }).getByRole("button", { name: new RegExp(modelId) });
  await row.scrollIntoViewIfNeeded();
  await row.click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("heading", { name: "供应商设置" }).waitFor();
  return sheet;
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
    name: "预置供应商详情：模型列表按类型分组，筛选「已启用」后停用的模型不再出现，长名称与状态标记换行",
    path: PROVIDERS_PATH,
    api: { ...PROVIDERS_WITH_KEYS, ...MODEL_LIST_STATES },
    ready: presetDetailReady,
    act: async (page) => {
      const models = page.getByRole("region", { name: "模型" });
      await models.scrollIntoViewIfNeeded();
      await expect(models.getByText("已下架")).toBeVisible();
      await models.getByRole("button", { name: /已启用/ }).click();
      await expect(models.getByText("已下架")).toHaveCount(0);
      await expect(models.getByRole("switch").first()).toBeChecked();
      // 回到「全部」，截长名称与状态标记所在的行
      await models.getByRole("button", { name: /全部/ }).click();
      const legacy = models.getByRole("listitem").filter({ hasText: "已下架" });
      await legacy.scrollIntoViewIfNeeded();
      await expect(legacy).toBeInViewport();
    },
    screenshot: { name: "providers-preset-models", target: (page) => page.getByRole("main") },
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
    name: "自定义供应商详情：模型列表的长模型 ID 换行，正文在详情栏里滚动，底部保存栏始终可见",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const main = page.getByRole("main");
      await main.getByLabel("名称").fill("自建网关");
      const models = main.getByRole("region", { name: "模型" });
      await models.scrollIntoViewIfNeeded();
      await expect(models.getByRole("listitem")).toHaveCount(9);
      const save = page.getByRole("button", { name: "保存" });
      await expect(save).toBeInViewport({ ratio: 1 });
      await expect(save).toBeEnabled();
    },
    screenshot: { name: "providers-custom-detail", target: (page) => page.getByRole("main") },
  },
  {
    name: "自定义供应商「获取模型」：已识别与未识别分两组，长模型 ID 换行，每行可选调用端点",
    path: CUSTOM_PROVIDER_PATH,
    api: { ...CUSTOM_PROVIDER_DETAIL, ...CUSTOM_DISCOVERY },
    ready: customDetailReady,
    act: async (page) => {
      await page.getByRole("region", { name: "模型" }).getByRole("button", { name: "获取模型" }).click();
      const dialog = page.getByRole("dialog");
      const recognized = dialog.getByRole("region", { name: /已识别/ });
      const unrecognized = dialog.getByRole("region", { name: /未识别/ });
      await expect(recognized.getByRole("checkbox", { name: /gpt-5\.5/ })).toBeChecked();
      await expect(unrecognized.getByRole("checkbox", { name: /acme-video-pro/ })).not.toBeChecked();
      await expect(dialog.getByText("另有 1 个已添加的模型未列出。")).toBeVisible();
      await expect(dialog.getByRole("button", { name: "添加 2 个模型" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "providers-fetch-models-dialog", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "「添加模型」：输入模型 ID 后可按 ID 添加，或从模型目录选择",
    path: CUSTOM_PROVIDER_PATH,
    api: {
      ...CUSTOM_PROVIDER_DETAIL,
      // 打开时搜索词为空，列出整个目录；输入后按名字搜索
      "GET /api/v1/catalog/entries": { status: 200, body: { entries: SEEDANCE_ENTRIES } },
      "GET /api/v1/catalog/entries?q=seedance": { status: 200, body: { entries: SEEDANCE_ENTRIES } },
    },
    ready: customDetailReady,
    act: async (page) => {
      await page.getByRole("region", { name: "模型" }).getByRole("button", { name: "添加模型" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByPlaceholder("搜索模型名称，或输入模型 ID").fill("seedance");
      await expect(dialog.getByRole("option", { name: /添加「seedance」/ })).toBeVisible();
      await expect(dialog.getByRole("option", { name: /Seedance 2\.0 Pro/ })).toBeVisible();
      await expect(dialog).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "providers-add-model-dialog", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "自定义供应商的模型 Sheet：参数全部展开时正文在 Sheet 里滚动，保存栏始终可见",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const sheet = await openModelSheet(page, KLING_ID);
      await expect(sheet.getByText("再乘供应商倍率 0.9，最终为 1.125。")).toBeVisible();
      await sheet.getByRole("button", { name: "修改「按分辨率限定时长」" }).click();
      await sheet.getByRole("button", { name: "修改「音轨形态」" }).click();
      await sheet.getByRole("textbox", { name: "模型 ID" }).fill(`${KLING_ID}-relay`);
      const info = sheet.getByRole("heading", { name: "模型信息" });
      await info.scrollIntoViewIfNeeded();
      await expect(info).toBeInViewport();
      const save = sheet.getByRole("button", { name: "保存" });
      await expect(save).toBeInViewport({ ratio: 1 });
      await expect(save).toBeEnabled();
    },
    screenshot: { name: "providers-custom-model-sheet", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "自定义供应商的模型 Sheet：打开调用端点下拉，按媒体分组的选项留在视口内",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const sheet = await openModelSheet(page, "gpt-image-1");
      // 用键盘打开：鼠标停在触发器上时它的悬停底色只是瞬时状态，不拿来判对比度
      const trigger = sheet.getByRole("combobox", { name: "调用端点" });
      await trigger.focus();
      await trigger.press("Enter");
      const listbox = page.getByRole("listbox");
      await expect(listbox.getByRole("option", { name: /OpenAI 图片/ }).first()).toBeVisible();
      await expect(listbox).toBeInViewport();
    },
    // 下拉渲染在弹层里，截 Sheet 连同盖在上面的下拉一起看
    screenshot: { name: "providers-custom-endpoint-select", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "模型 Sheet：价格编辑器点开条件标签，选项留在视口内",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const sheet = await openModelSheet(page, KLING_ID);
      const tag = sheet.getByRole("button", { name: /修改条件：分辨率：720p/ }).first();
      await tag.scrollIntoViewIfNeeded();
      await tag.click();
      const popover = page.locator('[data-slot="popover-content"]');
      await expect(popover.getByRole("button", { name: "2160p" })).toBeVisible();
      await expect(popover).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "模型 Sheet：「从『供应商』移除」的确认框列出在用的设置与项目",
    path: CUSTOM_PROVIDER_PATH,
    api: {
      ...CUSTOM_PROVIDER_DETAIL,
      [`GET /api/v1/providers/custom-${CUSTOM_PROVIDER_ID}/model-references?model_id=${KLING_ID}`]: {
        status: 200,
        body: {
          settings: ["default_video_backend", "default_video_backend_i2v"],
          projects: [
            { name: "demo", title: "星海列车：一部关于远方与归途的长篇连载动画短片（第一季）" },
            { name: "demo-2", title: null },
          ],
        },
      },
    },
    ready: customDetailReady,
    act: async (page) => {
      const sheet = await openModelSheet(page, KLING_ID);
      const remove = sheet.getByRole("button", { name: /^从「/ });
      await remove.scrollIntoViewIfNeeded();
      await remove.click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByText("项目：demo-2")).toBeVisible();
      await expect(dialog.getByRole("button", { name: "移除" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "providers-model-remove-dialog", target: (page) => page.getByRole("alertdialog") },
  },
  {
    name: "模型 Sheet：「恢复官方数据」的确认框写明无法撤销与一起恢复的供应商",
    path: CUSTOM_PROVIDER_PATH,
    api: CUSTOM_PROVIDER_DETAIL,
    ready: customDetailReady,
    act: async (page) => {
      const sheet = await openModelSheet(page, KLING_ID);
      const restore = sheet.getByRole("button", { name: "恢复官方数据" });
      await restore.scrollIntoViewIfNeeded();
      await restore.click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByText(/无法撤销/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "恢复官方数据" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "providers-model-restore-dialog", target: (page) => page.getByRole("alertdialog") },
  },
  {
    name: "模型 Sheet：「手动填写模型信息」对话框的字段随类型展开，正文在对话框里滚动，确定按钮留在视口内",
    path: CUSTOM_PROVIDER_PATH,
    api: {
      ...CUSTOM_PROVIDER_DETAIL,
      "GET /api/v1/catalog/new-entry-hints?name=gpt-image-1&media_type=image": {
        status: 200,
        body: { existing: null, durations: null },
      },
    },
    ready: customDetailReady,
    act: async (page) => {
      const sheet = await openModelSheet(page, "gpt-image-1");
      await sheet.getByRole("button", { name: "手动填写" }).click();
      const dialog = page.getByRole("dialog", { name: "手动填写模型信息" });
      await dialog.getByLabel("模型厂商").fill("openai-relay");
      await expect(dialog.getByText("不填价格时，调用费用记为未定价。")).toBeAttached();
      await expect(dialog.getByRole("button", { name: "确定" })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "确定" })).toBeEnabled();
    },
    screenshot: { name: "providers-manual-entry-dialog", target: (page) => page.getByRole("dialog", { name: "手动填写模型信息" }) },
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
