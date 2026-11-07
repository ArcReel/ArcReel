import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

export function viewport(page: Page) {
  const size = page.viewportSize();
  if (!size) throw new Error("没有视口尺寸");
  return size;
}

export async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("元素不可见");
  return rect;
}

/** Await finite entrance animations only, excluding task-indicator loops and never-ending scroll-driven scroll-fade animations. */
export async function waitForEntrance(target: Locator) {
  await target.evaluate(async (el) => {
    // Yield one frame, allowing newly mounted overlay styles/animations to enter DocumentTimeline.
    for (;;) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const animations = el.getAnimations({ subtree: true }).filter((animation) =>
        animation.timeline instanceof DocumentTimeline &&
        Number.isFinite(animation.effect?.getComputedTiming().endTime) &&
        (animation.playState === "running" || animation.pending),
      );
      if (animations.length === 0) return;
      await Promise.allSettled(animations.map((animation) => animation.finished));
    }
  });
}

/** Compact Agent panels cover canvas right sides; close before interacting there. */
export async function clearAgentOverlay(page: Page) {
  if (viewport(page).width >= 1280) return;
  const toggle = page.getByRole("button", { name: "Agent", exact: true });
  if (await toggle.getAttribute("aria-pressed") === "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
}
