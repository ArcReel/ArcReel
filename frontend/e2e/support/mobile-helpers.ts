import { expect } from "@playwright/test";
import { inspectLayout } from "./layout-probe.ts";
import { FIXED_NOW, RECORDED_ACCESS_TOKEN, loadRecordedResponses, recordedKey } from "./recorded.ts";

// 移动端与平板验收共用：录制响应打桩、登录态注入、等动画收敛、溢出与触控目标断言。

const RECORDED = loadRecordedResponses();

export async function stubApi(page: import("@playwright/test").Page) {
  await page.clock.setFixedTime(new Date(FIXED_NOW));
  await page.route(
    (url) => url.hostname !== "127.0.0.1" && url.hostname !== "localhost",
    (route) => route.abort(),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const response = RECORDED.get(recordedKey(request.method(), request.url()));
    if (!response) {
      await route.fulfill({ status: 501, json: { detail: `没有录制 ${request.method()} ${request.url()}` } });
      return;
    }
    if (typeof response.body === "string") {
      await route.fulfill({ status: response.status, body: response.body, contentType: "text/plain; charset=utf-8" });
      return;
    }
    await route.fulfill({ status: response.status, json: response.body });
  });
}

export async function signIn(page: import("@playwright/test").Page) {
  await page.addInitScript((token) => localStorage.setItem("arcreel_auth_token", token), RECORDED_ACCESS_TOKEN);
}

export async function open(page: import("@playwright/test").Page, path: string) {
  await stubApi(page);
  await signIn(page);
  await page.goto(path);
  await page.locator("body").waitFor();
  // 等入场动画结束，布局探针读到的是最终状态。
  await page.evaluate(async () => {
    for (;;) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const animations = document
        .getAnimations()
        .filter((animation) => animation.timeline instanceof DocumentTimeline && animation.playState === "running");
      if (animations.length === 0) return;
      await Promise.allSettled(animations.map((animation) => animation.finished));
    }
  });
}

export async function expectNoHorizontalScroll(page: import("@playwright/test").Page) {
  const report = await page.evaluate(inspectLayout);
  expect.soft(report.documentScrollWidth, "文档出现横向滚动").toBe(report.viewportWidth);
  expect.soft(report.strayScrollX, "纵向滚动区被宽内容撑出横向滚动").toEqual([]);
}

/** 可见的可点控件（按钮、链接、开关、输入）命中区不小于 44×44。 */
export async function expectTouchTargets(page: import("@playwright/test").Page) {
  const small = await page.evaluate(() => {
    const selector = 'button, a[href], [role="button"], [role="tab"], input:not([type="hidden"]), [data-slot="toggle"]';
    const out: { label: string; width: number; height: number }[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      if (!el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
      if (el.closest("[inert]")) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      // sr-only 的单选框等：1×1 视觉隐藏，实际点击目标是它旁边的可见标签。
      if (rect.width <= 1 && rect.height <= 1) continue;
      // 正文里的行内链接：WCAG 2.5.8 明确豁免「位于句子或文字块中」的链接，
      // 它们的高度就是行高，强行撑到 44px 会破坏排版。
      const style = getComputedStyle(el);
      if (el.tagName === "A" && style.display.startsWith("inline") && !style.display.startsWith("inline-")) continue;
      // 允许 1px 取整误差
      if (rect.width < 43 || rect.height < 43) {
        out.push({
          label: (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 40),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }
    }
    return out;
  });
  expect.soft(small, "以下控件的触控命中区小于 44×44").toEqual([]);
}
