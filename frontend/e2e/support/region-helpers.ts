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

/** 只等有限入场动画，不等任务指示器的循环动画，也不等 scroll-fade 这类随滚动推进、永不结束的动画。 */
export async function waitForEntrance(target: Locator) {
  await target.evaluate(async (el) => {
    // 先交出一帧，使新挂载弹层的样式与动画进入 DocumentTimeline。
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

/** 紧凑档 Agent 面板覆盖画布右侧，操作该区域前收起它。 */
export async function clearAgentOverlay(page: Page) {
  if (viewport(page).width >= 1280) return;
  const toggle = page.getByRole("button", { name: "Agent", exact: true });
  if (await toggle.getAttribute("aria-pressed") === "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
}
