import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

const EPISODE_PATH = "/app/projects/demo/episodes/1";
const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const NINE_LINES = Array.from(
  { length: 9 },
  (_, i) => `第 ${i + 1} 行：夜色压低了城墙的轮廓，巡夜人提着灯从箭楼下走过，风把旗子吹得猎猎作响。`,
).join("\n");

function narrationField(page: Page): Locator {
  return page.getByRole("dialog").getByRole("textbox", { name: "旁白正文" });
}

async function openNarrationDialog(page: Page) {
  await page.getByRole("button", { name: "新增第一个分镜" }).click();
  await narrationField(page).waitFor();
}

async function expectAllLinesReachable(field: Locator) {
  const box = await field.evaluate((el) => {
    const style = getComputedStyle(el);
    return {
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      overflowY: style.overflowY,
      atMaxHeight: style.maxHeight !== "none" && el.getBoundingClientRect().height >= parseFloat(style.maxHeight) - 1,
    };
  });
  if (box.scrollHeight > box.clientHeight + 1) {
    expect(box.atMaxHeight, "内容超出时输入框应已撑到上限").toBe(true);
    expect(["auto", "scroll"], "撑到上限后超出部分应能在内部滚动").toContain(box.overflowY);
  }
  expect(box.clientHeight).toBeGreaterThan(80);
}

defineRegionScenarios("自动撑高输入框", [
  {
    name: "弹层中写入 9 行文本后撑开到上限并可内部滚动",
    path: EPISODE_PATH,
    api: API,
    ready: async (page) => {
      await page.getByRole("button", { name: "新增第一个分镜" }).waitFor();
    },
    act: async (page) => {
      await openNarrationDialog(page);
      await narrationField(page).fill(NINE_LINES);
      await expectAllLinesReachable(narrationField(page));
    },
    screenshot: { name: "auto-textarea-nine-lines", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "容器变窄后重新计算高度",
    path: EPISODE_PATH,
    api: API,
    ready: async (page) => {
      await page.getByRole("button", { name: "新增第一个分镜" }).waitFor();
    },
    act: async (page) => {
      await openNarrationDialog(page);
      const field = narrationField(page);
      await field.fill(NINE_LINES.split("\n").slice(0, 2).join("\n"));
      const before = await field.evaluate((el) => el.getBoundingClientRect().height);
      await field.evaluate((el) => {
        const column = el.parentElement;
        if (column) column.style.width = `${column.clientWidth / 2}px`;
      });
      await expect.poll(() => field.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThan(before + 1);
      await expectAllLinesReachable(field);
    },
  },
]);
