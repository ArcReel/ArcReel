import type { Page } from "@playwright/test";
import { loadRecordedResponses } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 项目设置：侧栏分页（项目 / Agent 两组）、外壳底行的保存栏、风格对话框、模型分页的覆盖来源、
// 配音分页与 Agent 配置的重置确认。项目记忆的编辑器由记忆编辑器区域重做，这里只探测入口分页。

const RECORDED = loadRecordedResponses();
const PROJECT_KEY = "GET /api/v1/projects/demo";

function settings(tab?: string) {
  return tab ? `/app/projects/demo/settings?tab=${tab}` : "/app/projects/demo/settings";
}

/** 在录制的演示项目上改几个字段。 */
function projectWith(fields: Record<string, unknown>): ApiOverrides {
  const recorded = RECORDED.get(PROJECT_KEY)!.body as { project: Record<string, unknown> };
  return { [PROJECT_KEY]: { status: 200, body: { ...recorded, project: { ...recorded.project, ...fields } } } };
}

// 压力变体：三家供应商各十个模型，名称都很长；项目覆盖了视频与图片的细分项，细分区自动展开。
const LONG_PROVIDERS: [string, string][] = [
  ["gemini-aistudio", "AI Studio"],
  ["ark", "火山方舟 · 华北二区专属算力集群（企业版）"],
  ["custom-12", "自建 OpenAI 兼容网关 · 华东二区备用线路（按量计费）"],
];

function longModels(kind: string) {
  return LONG_PROVIDERS.flatMap(([provider]) =>
    Array.from({ length: 10 }, (_, i) => `${provider}/${kind}-model-${i + 1}-with-a-rather-long-identifier-preview`),
  );
}

function manyModelsOverrides(): ApiOverrides {
  const config = RECORDED.get("GET /api/v1/system/config")!.body as {
    settings: Record<string, unknown>;
    options: Record<string, unknown>;
  };
  const [video, image, text, audio] = ["video", "image", "text", "audio"].map(longModels);
  const modelNames = Object.fromEntries(
    [...video, ...image, ...text, ...audio].map((id, i) => [id, `超长模型名称 ${i + 1} · 高清长时长电影级生成（预览版）`]),
  );
  return {
    "GET /api/v1/system/config": {
      status: 200,
      body: {
        settings: { ...config.settings, default_video_backend: video[0], default_image_backend: image[0] },
        options: {
          ...config.options,
          video_backends: video,
          image_backends: image,
          text_backends: text,
          audio_backends: audio,
          model_names: modelNames,
        },
      },
    },
    "GET /api/v1/system/config/model-candidates": {
      status: 200,
      body: {
        image: { default: image, buckets: { t2i: image, i2i: image } },
        video: { default: video, buckets: { i2v: video, r2v: video } },
        provider_names: Object.fromEntries(LONG_PROVIDERS),
        model_names: modelNames,
      },
    },
    // 压力数据里的模型不在录制环境的供应商目录中，能力查询照实返回无法解析
    [`GET /api/v1/projects/demo/video-capabilities?resolution=&uses_reference_images=false&video_backend=${encodeURIComponent(video[0])}`]:
      RECORDED.get("GET /api/v1/projects/demo/video-capabilities")!,
    ...projectWith({
      video_provider_r2v: video[12],
      image_provider_i2i: image[21],
      default_text_backend: text[3],
      video_generate_audio: false,
    }),
  };
}

// Agent 配置：很多定制文件，路径很长。
const CUSTOMIZED_PROFILE: ApiOverrides = {
  "GET /api/v1/projects/demo/agent-profile": {
    status: 200,
    body: {
      customized: true,
      customized_files: [
        "CLAUDE.md",
        ...Array.from(
          { length: 24 },
          (_, i) => `.claude/agents/custom-subagent-${i + 1}-with-a-very-long-descriptive-name-for-wrapping.md`,
        ),
      ],
    },
  },
};

async function waitForAnimations(page: Page) {
  // 弹层淡入、保存按钮切换启用色时的过渡中间色会被 axe 判为对比度不足，等动画结束再探测。
  await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState !== "running"));
}

const sidebar = (page: Page) => page.getByRole("navigation", { name: "项目设置" });

async function tabReady(page: Page, title: string) {
  await page.getByRole("heading", { level: 2, name: title }).waitFor();
}

defineRegionScenarios("项目设置", [
  {
    name: "基础分页：改动后侧栏标出修改，外壳底行的保存栏在表单滚到底时完整可见",
    path: settings(),
    ready: (page) => tabReady(page, "基础"),
    act: async (page) => {
      await page.getByText("横屏 16:9").click();
      await expect(sidebar(page).getByRole("link", { name: /^基础.*有未保存的修改/ })).toBeVisible();
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("button", { name: "保存" })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
      await waitForAnimations(page);
    },
    screenshot: { name: "project-settings-basics", target: (page) => page.getByRole("main") },
  },
  {
    name: "广告项目的基础分页：目标总时长自定义输入无效时的行内提示",
    path: settings(),
    api: projectWith({ content_mode: "ad", target_duration: 45 }),
    ready: (page) => tabReady(page, "基础"),
    act: async (page) => {
      const group = page.getByRole("radiogroup", { name: "目标总时长" });
      await expect(group.getByRole("radio", { name: "自定义" })).toBeChecked();
      await page.getByRole("spinbutton", { name: /目标总时长/ }).fill("0");
      await page.getByRole("main").evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await waitForAnimations(page);
    },
  },
  {
    name: "风格分页：「更换」打开风格对话框，只有对话框正文滚动",
    path: settings("style"),
    api: projectWith({ style_template_id: "live_premium_drama" }),
    ready: (page) => tabReady(page, "风格"),
    act: async (page) => {
      await page.getByRole("button", { name: "更换" }).click();
      const dialog = page.getByRole("dialog", { name: "更换风格" });
      await dialog.waitFor();
      await waitForAnimations(page);
      const body = dialog.locator('[data-slot="dialog-body"]');
      await body.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(dialog.getByRole("button", { name: "使用此风格" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "project-settings-style-dialog", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "模型分页（压力）：覆盖说明条、通道来源与展开的细分区，滚到底仍可保存",
    path: settings("models"),
    api: manyModelsOverrides(),
    ready: async (page) => {
      await tabReady(page, "模型");
      await page.getByText(/本项目覆盖了 4 项全局默认/).waitFor();
    },
    act: async (page) => {
      await expect(sidebar(page).getByRole("link", { name: /^模型 4 项覆盖/ })).toBeVisible();
      await page.getByRole("button", { name: "文本模型：恢复全局" }).click();
      await page.getByText(/本项目覆盖了 3 项全局默认/).waitFor();
      await page.getByRole("main").evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(page.getByRole("button", { name: "保存" })).toBeInViewport({ ratio: 1 });
      await waitForAnimations(page);
    },
    screenshot: { name: "project-settings-models", target: (page) => page.getByRole("main") },
  },
  {
    name: "模型分页：打开默认视频模型下拉，弹层留在视口内",
    path: settings("models"),
    api: manyModelsOverrides(),
    ready: (page) => tabReady(page, "模型"),
    act: async (page) => {
      await page.getByRole("combobox", { name: "默认视频模型" }).click();
      await page.getByRole("listbox").waitFor();
      await waitForAnimations(page);
    },
  },
  {
    name: "配音分页：参考生视频项目使用 TTS 配音，显示角色声音绑定",
    path: settings("voice"),
    api: {
      "GET /api/v1/system/tts-model-capabilities?backend=gemini-aistudio/gemini-2.5-flash-preview-tts": {
        status: 200,
        body: { supports_speed: true },
      },
      ...projectWith({
        generation_mode: "reference_video",
        narration_delivery: "use_tts",
        audio_backend: "gemini-aistudio/gemini-2.5-flash-preview-tts",
        narration_voice: "Kore-with-a-very-long-custom-voice-identifier-for-wrapping-checks",
        narration_speed: 1.1,
      }),
    },
    ready: (page) => tabReady(page, "配音"),
    act: async (page) => {
      await page.getByRole("radiogroup", { name: "角色声音绑定方式" }).waitFor();
      await page.getByRole("main").evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
    },
    screenshot: { name: "project-settings-voice", target: (page) => page.getByRole("main") },
  },
  {
    name: "项目记忆分页：入口在 Agent 组，不显示外壳保存栏",
    path: settings("memory"),
    ready: (page) => tabReady(page, "项目记忆"),
    act: async (page) => {
      await expect(page.getByRole("button", { name: "保存" })).toHaveCount(0);
    },
  },
  {
    name: "Agent 配置（压力）：很多定制文件，打开重置确认",
    path: settings("agent"),
    api: CUSTOMIZED_PROFILE,
    ready: async (page) => {
      await tabReady(page, "Agent 配置");
      await page.getByText("CLAUDE.md").waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: "重置为内置配置" }).click();
      await page.getByRole("alertdialog", { name: "重置 Agent 配置？" }).waitFor();
      await waitForAnimations(page);
    },
    screenshot: { name: "project-settings-agent-reset", target: (page) => page.getByRole("alertdialog") },
  },
  {
    name: "侧栏：模型分页的覆盖数与修改标记",
    path: settings("models"),
    api: manyModelsOverrides(),
    ready: (page) => tabReady(page, "模型"),
    act: async (page) => {
      await page.getByRole("button", { name: "图片模型：恢复全局" }).click();
      await expect(sidebar(page).getByRole("link", { name: /^模型.*有未保存的修改/ })).toBeVisible();
      await waitForAnimations(page);
    },
    screenshot: { name: "project-settings-sidebar", target: (page) => sidebar(page) },
  },
]);
