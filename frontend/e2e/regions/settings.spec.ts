import type { Page } from "@playwright/test";
import { loadRecordedResponses } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 页面外壳与全局设置导航：三档容器（全出血、限宽、铺满）、外壳保存栏与侧栏；
// 「默认模型」「关于」「提示词模版」分区与配置问题的就地提示。
// 其他分区的内容由各自的区域重做，这里不截它们的整块内容。供应商分区的场景在 providers.spec.ts。

const RECORDED = loadRecordedResponses();

function settings(section: string) {
  return `/app/settings?section=${section}`;
}

// 压力变体：三家供应商各十个模型，供应商名与模型名都很长；视频与图片都有细分项已配置，
// 细分区自动展开。
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
        settings: {
          ...config.settings,
          default_video_backend: video[0],
          default_video_backend_r2v: video[12],
          default_image_backend: image[0],
          default_image_backend_i2i: image[21],
          default_text_backend: text[3],
          default_audio_backend: audio[5],
          narration_voice: "zh-CN-XiaoxiaoMultilingualNeural",
        },
        options: {
          ...config.options,
          video_backends: video,
          image_backends: image,
          text_backends: text,
          audio_backends: audio,
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
  };
}

// 「关于」：有新版本，发布说明很长（多段、长链接）。代码块的横向滚动属于 Markdown 渲染的共用样式，不在这里覆盖。版本信息在录制环境里访问外网，固定由场景提供。
const LONG_RELEASE: ApiOverrides = {
  "GET /api/v1/system/version": {
    status: 200,
    body: {
      current: { version: "0.32.0" },
      latest: {
        version: "0.33.0",
        tag_name: "v0.33.0",
        name: "0.33.0",
        body: [
          "## 新功能",
          ...Array.from({ length: 24 }, (_, i) => `- 第 ${i + 1} 项改进：分镜画布在矮窗口下保留完整的编辑区域，长提示词不再撑开内容列，并补齐键盘操作`),
          "",
          "## 升级说明",
          "升级前请备份数据目录。完整说明见 https://github.com/ArcReel/ArcReel/releases/tag/v0.33.0-with-a-very-long-anchor-for-wrapping-checks",
        ].join("\n"),
        html_url: "https://github.com/ArcReel/ArcReel/releases/tag/v0.33.0",
        published_at: "2025-12-30T08:00:00Z",
      },
      has_update: true,
      checked_at: "2026-01-01T07:00:00Z",
      update_check_error: null,
    },
  },
};

const PROMPT_TEMPLATES = settings("prompt-templates");

async function settingsReady(page: Page) {
  await page.getByRole("navigation", { name: "设置" }).getByRole("link", { name: "关于" }).waitFor();
}

defineRegionScenarios("全局设置", [
  {
    name: "默认落在全出血档的供应商，侧栏与供应商列表各自滚动",
    path: "/app/settings",
    ready: async (page) => {
      await settingsReady(page);
      const sidebar = page.getByRole("navigation", { name: "设置" });
      await expect(sidebar.getByRole("link", { name: "供应商" })).toHaveAttribute("aria-current", "page");
    },
    screenshot: { name: "settings-sidebar", target: (page) => page.getByRole("navigation", { name: "设置" }) },
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
  {
    name: "缺少供应商时提示只出现在供应商分区顶部，侧栏只标记问题所属的分区",
    path: "/app/settings",
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("note", { name: "配置不完整" }).waitFor();
    },
    act: async (page) => {
      const nav = page.getByRole("navigation", { name: "设置" });
      // 录制环境既没有可用的供应商，也没有 Agent 供应商。
      const flagged = nav.getByRole("link").filter({ has: page.getByRole("img", { name: "配置不完整" }) });
      await expect(flagged).toHaveText(["供应商", "ArcReel Agent"]);
      await expect(page.getByRole("note", { name: "配置不完整" })).toBeInViewport();
    },
    screenshot: { name: "settings-config-issue-notice", target: (page) => page.getByRole("note", { name: "配置不完整" }) },
  },
  {
    name: "没有可用供应商时，默认模型各通道显示空状态并链接到供应商",
    path: settings("default-models"),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("heading", { name: "默认模型", level: 2 }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await expect(page.getByRole("note", { name: "配置不完整" })).toHaveCount(0);
      await expect(main.getByRole("link", { name: "前往「供应商」配置" })).toHaveCount(4);
    },
    screenshot: { name: "settings-default-models-empty", target: (page) => page.getByRole("main") },
  },
  {
    name: "模型多且名称长时，默认模型的细分区展开、长名称截断不撑宽内容列",
    path: settings("default-models"),
    api: manyModelsOverrides(),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("combobox", { name: "默认视频模型" }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await expect(main.getByRole("button", { name: /按用途指定模型/ }).first()).toHaveAttribute("aria-expanded", "true");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(main.getByRole("spinbutton", { name: "配音语速（可选）" })).toBeInViewport();
    },
    screenshot: { name: "settings-default-models", target: (page) => page.getByRole("main") },
  },
  {
    name: "打开模型多的默认视频模型下拉，列表在弹层里滚动到底，弹层留在视口内",
    path: settings("default-models"),
    api: manyModelsOverrides(),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("combobox", { name: "默认视频模型" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("combobox", { name: "默认视频模型" }).click();
      const listbox = page.getByRole("listbox");
      await expect(page.getByRole("combobox", { name: "搜索模型或供应商" })).toBeFocused();
      await expect(listbox).toBeInViewport({ ratio: 1 });
      await listbox.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(listbox.getByRole("option").last()).toBeInViewport();
    },
    screenshot: { name: "settings-default-models-combobox" },
  },
  {
    name: "编辑默认模型后切换分区，弹出未保存修改的拦截对话框",
    path: settings("default-models"),
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("textbox", { name: "视频轮询超时（秒）" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("textbox", { name: "视频轮询超时（秒）" }).fill("7200");
      await page.getByRole("navigation", { name: "设置" }).getByRole("link", { name: "关于" }).click();
      const dialog = page.getByRole("alertdialog", { name: "有未保存的修改" });
      await expect(dialog).toBeVisible();
      await expect(page).toHaveURL(/section=default-models/);
    },
  },
  {
    name: "关于：版本卡的发布说明默认收起",
    path: settings("about"),
    api: LONG_RELEASE,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("region", { name: "当前版本" }).waitFor();
    },
    act: async (page) => {
      await expect(page.getByRole("button", { name: /发布说明/ })).toHaveAttribute("aria-expanded", "false");
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(main.getByRole("link", { name: "https://github.com/ArcReel/ArcReel" })).toBeInViewport();
    },
    screenshot: { name: "settings-about-version", target: (page) => page.getByRole("region", { name: "当前版本" }) },
  },
  {
    name: "关于：展开很长的发布说明，内容随分区滚动到底，长链接不撑宽",
    path: settings("about"),
    api: LONG_RELEASE,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("region", { name: "当前版本" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: /发布说明/ }).click();
      const release = page.getByRole("link", { name: "打开 GitHub Release" });
      // 等懒加载 Markdown 完成排版，再滚到位于正文之后的 Release 链接。
      await expect(page.locator(".markdown-body")).toBeVisible();
      await release.scrollIntoViewIfNeeded();
      await expect(release).toBeInViewport();
    },
  },
  {
    name: "提示词模版列表展开画风组后滚动到底",
    path: PROMPT_TEMPLATES,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("heading", { name: "提示词模版", level: 2 }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await main.getByRole("button", { name: "画风", exact: true }).click();
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      const style = main.getByRole("region", { name: "画风" });
      await expect(style.getByRole("listitem").last()).toBeInViewport();
    },
  },
  {
    name: "提示词模版列表按创作类型筛选",
    path: PROMPT_TEMPLATES,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("heading", { name: "提示词模版", level: 2 }).waitFor();
    },
    act: async (page) => {
      const contentMode = page.getByRole("group", { name: "创作类型" });
      await contentMode.getByRole("button", { name: "旁白/解说" }).click();
      await expect(contentMode.getByRole("button", { name: "旁白/解说" })).toHaveAttribute("aria-pressed", "true");
    },
    screenshot: { name: "settings-prompt-templates", target: (page) => page.getByRole("main") },
  },
  {
    name: "提示词模版详情：展开片段与输出结构后滚动到底",
    path: `${PROMPT_TEMPLATES}&template=text/episode_plan`,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("heading", { name: "分集规划", level: 2 }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await main.getByRole("button", { name: /variant\("text\/episode_plan\/intro"/ }).click();
      await main.getByRole("button", { name: /输出结构/ }).click();
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(main.getByRole("table")).toBeInViewport();
    },
    screenshot: { name: "settings-prompt-template-detail", target: (page) => page.getByRole("main") },
  },
  {
    name: "提示词模版详情：打开片段的取值下拉",
    path: `${PROMPT_TEMPLATES}&template=text/episode_plan`,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("heading", { name: "分集规划", level: 2 }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await main.getByRole("button", { name: /variant\("text\/episode_plan\/intro"/ }).click();
      await main.getByRole("combobox", { name: "源文件类型" }).click();
      await expect(page.getByRole("listbox")).toBeInViewport({ ratio: 1 });
    },
  },
  {
    name: "提示词片段详情列出引用它的模版",
    path: `${PROMPT_TEMPLATES}&partial=shared/additional_instructions`,
    ready: async (page) => {
      await settingsReady(page);
      await page.getByRole("heading", { name: "shared/additional_instructions", level: 2 }).waitFor();
    },
    act: async (page) => {
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      const usages = main.locator("section").filter({ has: page.getByRole("heading", { name: "引用它的模版" }) });
      await expect(usages.getByRole("listitem").last()).toBeInViewport();
    },
  },
]);
