import type { Locator, Page } from "@playwright/test";
import { loadRecordedResponses } from "../support/recorded.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 新建项目向导：固定高度的 Dialog，三步共用一个滚动区，底部按钮不随步骤移动。
// 向导从项目大厅顶栏打开；大厅本身的布局由大厅区域负责，这里只截向导。

const RECORDED = loadRecordedResponses();
const LOBBY = "/app/projects";

const LONG_TITLE = "重生之皇后威武：一部关于宫廷权谋、家族兴衰与命运抗争的超长篇古装剧情改编作品（第一季）";

// 压力变体：可选模型很多、供应商名与模型名都很长，并配置了全局默认，第二步的模型通道全部展开。
function manyModels(): ApiOverrides {
  const config = RECORDED.get("GET /api/v1/system/config")!.body as {
    settings: Record<string, unknown>;
    options: Record<string, unknown>;
  };
  const ids = (kind: string) =>
    Array.from({ length: 24 }, (_, i) => `custom-12/${kind}-model-${i + 1}-with-a-rather-long-identifier-preview`);
  const [video, image, text, audio] = ["video", "image", "text", "audio"].map(ids);
  return {
    "GET /api/v1/system/config": {
      status: 200,
      body: {
        settings: { ...config.settings, default_image_backend: image[0], default_text_backend: text[3] },
        options: {
          ...config.options,
          video_backends: video,
          image_backends: image,
          text_backends: text,
          audio_backends: audio,
          provider_names: { "custom-12": "自建 OpenAI 兼容网关 · 华东二区备用线路（按量计费）" },
          model_names: Object.fromEntries(
            [...video, ...image, ...text, ...audio].map((id, i) => [id, `超长模型名称 ${i + 1} · 高清长时长电影级生成（预览版）`]),
          ),
        },
      },
    },
  };
}

async function lobbyReady(page: Page) {
  await page.getByRole("button", { name: "新建项目" }).first().waitFor();
}

function wizard(page: Page): Locator {
  return page.getByRole("dialog", { name: "新建项目" });
}

async function settle(locator: Locator) {
  await locator.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
}

async function openWizard(page: Page) {
  await page.getByRole("button", { name: "新建项目" }).first().click();
  await settle(wizard(page));
}

async function fillBasics(page: Page, { mode = "旁白/解说", route = "分镜图生视频" } = {}) {
  const dialog = wizard(page);
  await dialog.getByRole("textbox", { name: "项目标题" }).fill(LONG_TITLE);
  await dialog.getByRole("radio", { name: new RegExp(mode) }).check({ force: true });
  await dialog.getByRole("radio", { name: new RegExp(route) }).check({ force: true });
}

function nextButton(page: Page) {
  return wizard(page).getByRole("button", { name: /^(下一步|创建项目)$/ });
}

async function goNext(page: Page) {
  await nextButton(page).click();
}

/** 滚到正文最底部，确认最后一块内容可达，标题与底部按钮仍完整可见。 */
async function expectBodyScrollsToEnd(page: Page, last: Locator) {
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeInViewport();
  await expect(wizard(page).getByRole("heading", { name: "新建项目" })).toBeInViewport({ ratio: 1 });
  await expect(nextButton(page)).toBeInViewport({ ratio: 1 });
}

/** 截图前把正文滚回顶部，基线只记录每一步的首屏。 */
async function scrollBodyToTop(page: Page) {
  await wizard(page)
    .locator('[data-slot="dialog-body"]')
    .evaluate((el) => el.scrollTo({ top: 0 }));
}

defineRegionScenarios("新建项目向导", [
  {
    name: "第一步未填写时列出缺少的必填项，正文可滚到生成方式",
    path: LOBBY,
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      const dialog = wizard(page);
      await expect(nextButton(page)).toBeDisabled();
      await expect(dialog.getByText("还需要：项目标题、创作类型、生成方式")).toBeVisible();
      await expectBodyScrollsToEnd(page, dialog.getByRole("radio", { name: /参考生视频/ }).locator(".."));
      await scrollBodyToTop(page);
    },
    screenshot: { name: "create-project-basics", target: wizard },
  },
  {
    name: "第一步填满并展开宫格分镜后，正文仍可滚到底",
    path: LOBBY,
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      await fillBasics(page);
      const grid = wizard(page).getByRole("switch", { name: "多宫格分镜" });
      await grid.click();
      await expect(nextButton(page)).toBeEnabled();
      await expectBodyScrollsToEnd(page, grid);
    },
  },
  {
    name: "切换步骤时外框与底部按钮不移动，进入新步骤从顶部开始",
    path: LOBBY,
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      await fillBasics(page);
      const dialog = wizard(page);
      const frame = await dialog.boundingBox();
      const cancel = await dialog.getByRole("button", { name: "取消" }).boundingBox();
      const next = await nextButton(page).boundingBox();
      await dialog.getByRole("switch", { name: "多宫格分镜" }).scrollIntoViewIfNeeded();

      for (const step of ["生成设置", "风格"]) {
        await goNext(page);
        await expect(dialog.getByRole("listitem").filter({ hasText: step })).toHaveAttribute("aria-current", "step");
        expect(await dialog.boundingBox()).toEqual(frame);
        expect(await dialog.getByRole("button", { name: "取消" }).boundingBox()).toEqual(cancel);
        // 「下一步」与「创建项目」宽度不同，比较右边缘与纵向位置
        const box = await nextButton(page).boundingBox();
        expect(box && next).toBeTruthy();
        expect(box!.x + box!.width).toBeCloseTo(next!.x + next!.width, 0);
        expect(box!.y).toBeCloseTo(next!.y, 0);
        const scrollTop = await dialog.locator('[data-slot="dialog-body"]').evaluate((el) => el.scrollTop);
        expect(scrollTop).toBe(0);
      }
    },
  },
  {
    name: "第二步选择 TTS 配音且模型很多时，正文可滚到底",
    path: LOBBY,
    api: manyModels(),
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      await fillBasics(page, { route: "参考生视频" });
      await goNext(page);
      const dialog = wizard(page);
      await dialog.getByRole("radio", { name: "TTS 配音" }).check({ force: true });
      await expectBodyScrollsToEnd(page, dialog.getByLabel("配音语速（可选）"));
    },
    screenshot: { name: "create-project-generation", target: wizard },
  },
  {
    name: "第二步打开模型下拉，弹层留在视口内",
    path: LOBBY,
    api: manyModels(),
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      await fillBasics(page);
      await goNext(page);
      await wizard(page).getByRole("combobox").first().click();
      const listbox = page.getByRole("listbox");
      await settle(listbox);
      await expect(listbox).toBeInViewport();
    },
  },
  {
    name: "广告项目自定义目标总时长无效时说明需要修正的字段",
    path: LOBBY,
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      await fillBasics(page, { mode: "广告/短片" });
      await goNext(page);
      const dialog = wizard(page);
      await dialog.getByRole("radio", { name: "自定义" }).check({ force: true });
      await expect(dialog.getByText("请修正：目标总时长")).toBeVisible();
      await expect(nextButton(page)).toBeDisabled();
    },
    screenshot: { name: "create-project-ad-duration", target: wizard },
  },
  {
    name: "风格步骤只有一个滚动容器，模版网格可滚到底",
    path: LOBBY,
    ready: lobbyReady,
    act: async (page) => {
      await openWizard(page);
      await fillBasics(page);
      await goNext(page);
      await goNext(page);
      const dialog = wizard(page);
      await expect(nextButton(page)).toHaveText("创建项目");
      const scrollers = await dialog.evaluate((root) =>
        [root, ...root.querySelectorAll("*")]
          .filter((el) => {
            const { overflowY } = getComputedStyle(el);
            return (overflowY === "auto" || overflowY === "scroll") && el.scrollHeight > el.clientHeight + 1;
          })
          .map((el) => el.getAttribute("data-slot") ?? el.tagName),
      );
      expect(scrollers.length).toBeLessThanOrEqual(1);
      if (scrollers.length === 1) expect(scrollers[0]).toBe("dialog-body");
      const cards = dialog.getByRole("tabpanel").getByRole("button");
      await expectBodyScrollsToEnd(page, cards.last());
      await scrollBodyToTop(page);
    },
    screenshot: { name: "create-project-style", target: wizard },
  },
]);
