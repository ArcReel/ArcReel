import type { Locator, Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 弹层、提示与菜单原语：借集页顶栏「导出项目归档」的诊断对话框与下载提示，以及侧栏「添加一集」菜单验证。
// 大厅页尚未重做（文档本身滚动），整页探针会报出与弹层无关的问题，因此都放在集页上。
const EPISODE_PATH = "/app/projects/demo/episodes/1";
// 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在录制的项目数据上。
const EVENT_STREAM: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
};

const LONG_LOCATION =
  "projects/demo/episodes/episode_12/storyboards/scene_07/shot_03/variants/final_render_with_an_unusually_long_file_name_v12.png";

function diagnostics(prefix: string, count: number) {
  return Array.from({ length: count }, (_, i) => ({
    code: `${prefix}_${i + 1}`,
    message: `第 ${i + 1} 条：分镜引用的角色设定图不存在，导出后需要在角色库中重新上传或改为引用其他角色，否则生成视频时会跳过这一镜。`,
    location: `${LONG_LOCATION}#${i + 1}`,
  }));
}

function exportWith(diagnosticsBody: Record<string, unknown>): ApiOverrides {
  return {
    ...EVENT_STREAM,
    "POST /api/v1/projects/demo/export/token": {
      status: 200,
      body: { download_token: "e2e-token", expires_in: 300, diagnostics: diagnosticsBody },
    },
    // 下载链接是一次页面跳转；204 让浏览器留在当前页。
    "GET /api/v1/projects/demo/export": { status: 204, body: null },
  };
}

// 阻断、自动修复与警告三组，条目多且文字长，正文必须滚动。
const MANY_DIAGNOSTICS = exportWith({
  blocking: diagnostics("missing_asset", 12),
  auto_fixed: diagnostics("legacy_field", 4),
  warnings: diagnostics("unused_file", 12),
});
const FEW_DIAGNOSTICS = exportWith({ blocking: [], auto_fixed: diagnostics("legacy_field", 2), warnings: [] });

async function episodeReady(page: Page) {
  await page.getByRole("button", { name: "添加一集" }).waitFor();
}

async function exportProject(page: Page) {
  await page.getByRole("button", { name: "导出项目归档" }).click();
  await page.getByRole("button", { name: /仅当前版本/ }).click();
}

function diagnosticsDialog(page: Page): Locator {
  return page.getByRole("dialog", { name: "导出诊断" });
}

defineRegionScenarios("弹层与提示", [
  {
    name: "诊断条目多且长时，标题与关闭按钮留在视口内，只有正文滚动",
    path: EPISODE_PATH,
    api: MANY_DIAGNOSTICS,
    ready: episodeReady,
    act: async (page) => {
      await exportProject(page);
      // 先关掉同时发出的下载提示：它 5 秒后自动消失，留着会让截图不稳定。
      const region = page.getByRole("region", { name: "提示" });
      await region.hover();
      await region.getByRole("button", { name: "关闭提示" }).click();
      await expect(region.getByRole("dialog")).toHaveCount(0);

      const dialog = diagnosticsDialog(page);
      const title = dialog.getByRole("heading", { name: "导出诊断" });
      const close = dialog.getByRole("button", { name: "关闭" });
      await expect(title).toBeInViewport({ ratio: 1 });
      await expect(close).toBeInViewport({ ratio: 1 });

      // 滚到最后一条后，标题与关闭按钮仍完整可见：滚动的只是正文。
      const last = dialog.getByText(/^第 12 条/).last();
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport();
      await expect(title).toBeInViewport({ ratio: 1 });
      await expect(close).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "overlays-export-diagnostics", target: (page) => diagnosticsDialog(page) },
  },
  {
    name: "下载提示显示在诊断对话框之上，减少动态效果时出现过程不位移",
    path: EPISODE_PATH,
    api: FEW_DIAGNOSTICS,
    ready: episodeReady,
    act: async (page) => {
      // 逐帧记录提示的位置：从出现起采样 600ms，覆盖整个进场过程。
      await page.evaluate(() => {
        const tops: number[] = [];
        (window as unknown as { __toastTops: number[] }).__toastTops = tops;
        let firstSeen: number | null = null;
        const sample = (now: number) => {
          const toast = document.querySelector('[data-slot="toast"]');
          if (toast) {
            firstSeen ??= now;
            tops.push(toast.getBoundingClientRect().top);
          }
          if (firstSeen === null || now - firstSeen < 600) requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      await exportProject(page);

      const region = page.getByRole("region", { name: "提示" });
      // Base UI 的提示根节点是非模态 dialog，名称取自提示标题。
      const toast = region.getByRole("dialog", { name: "项目 ZIP 已开始下载，导出包包含 2 条诊断" });
      await expect(toast).toBeVisible();
      await expect(diagnosticsDialog(page)).toBeVisible();

      await expect
        .poll(() => page.evaluate(() => (window as unknown as { __toastTops: number[] }).__toastTops.length))
        .toBeGreaterThan(10);
      const tops = await page.evaluate(() => (window as unknown as { __toastTops: number[] }).__toastTops);
      expect(Math.max(...tops) - Math.min(...tops), "提示在进场过程中发生了位移").toBeLessThanOrEqual(1);

      // 指针停在提示上会暂停自动消失，截图与断言期间提示保持可见。
      await toast.hover();
      await expect(region.getByRole("button", { name: "关闭提示" })).toBeVisible();
    },
    screenshot: { name: "overlays-toast-over-dialog", target: (page) => page.getByRole("region", { name: "提示" }) },
  },
  {
    name: "打开侧栏「添加一集」菜单",
    path: EPISODE_PATH,
    api: EVENT_STREAM,
    ready: episodeReady,
    act: async (page) => {
      await page.getByRole("button", { name: "添加一集" }).click();
      const menu = page.getByRole("menu", { name: "添加一集" });
      await expect(menu.getByRole("menuitem", { name: "新建一集" })).toBeInViewport({ ratio: 1 });
      await expect(menu.getByRole("menuitem", { name: "上传集原文" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "overlays-action-menu", target: (page) => page.getByRole("menu", { name: "添加一集" }) },
  },
]);
