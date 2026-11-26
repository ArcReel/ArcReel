interface AcceptanceViewport {
  width: number;
  height: number;
  pullRequest: boolean;
}

const VIEWPORTS: AcceptanceViewport[] = [
  { width: 1024, height: 600, pullRequest: true },
  { width: 1280, height: 600, pullRequest: false },
  { width: 1440, height: 900, pullRequest: true },
  { width: 1920, height: 1080, pullRequest: false },
  { width: 2560, height: 1440, pullRequest: true },
];

export function projectName(viewport: AcceptanceViewport) {
  return `${viewport.width}x${viewport.height}`;
}

export function selectedViewports(selection = process.env.E2E_VIEWPORTS ?? "all") {
  if (selection === "all") return VIEWPORTS;
  if (selection === "screenshot") return VIEWPORTS.filter((viewport) => viewport.pullRequest);
  throw new Error(`E2E_VIEWPORTS 只接受 all 或 screenshot，收到 ${selection}`);
}
