// 验收视口：从最低支持的 1024×600 到验收上限 2560×1440。每档都跑溢出探针与 axe，
// screenshot 标出另外比对区域截图的三档。

export interface AcceptanceViewport {
  width: number;
  height: number;
  screenshot: boolean;
}

export const VIEWPORTS: AcceptanceViewport[] = [
  { width: 1024, height: 600, screenshot: true },
  { width: 1280, height: 600, screenshot: false },
  { width: 1440, height: 900, screenshot: true },
  { width: 1920, height: 1080, screenshot: false },
  { width: 2560, height: 1440, screenshot: true },
];

/** Playwright project 名，也是截图基线的目录名。 */
export function projectName(viewport: AcceptanceViewport) {
  return `${viewport.width}x${viewport.height}`;
}

/**
 * 本次运行的视口。`E2E_VIEWPORTS=screenshot` 只跑截图三档，CI 在 PR 上这样跑；
 * 不设置时跑全部五档，nightly 与本地默认如此。
 */
export function selectedViewports(selection = process.env.E2E_VIEWPORTS ?? "all") {
  if (selection === "all") return VIEWPORTS;
  if (selection === "screenshot") return VIEWPORTS.filter((viewport) => viewport.screenshot);
  throw new Error(`E2E_VIEWPORTS 只接受 all 或 screenshot，收到 ${selection}`);
}
