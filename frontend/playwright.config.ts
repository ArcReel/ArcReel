import { defineConfig } from "@playwright/test";
import { DEFAULT_E2E_PORT } from "./e2e/support/origin.ts";

// 页面级验收套件。CI 在与 @playwright/test 同版本的官方镜像里运行（.github/workflows/test.yml 的
// frontend-e2e job）；本地用 `pnpm e2e:server` 在同一镜像里起 run-server，再用 `pnpm e2e:remote`
// 连上去渲染，结果与 CI 一致。两处镜像版本都要跟 package.json 里的 @playwright/test 一起改。

// 预览端口与产物目录可用环境变量覆盖，供同一台机器上并行运行多份套件；不设置时与 CI 一致。
// 截图基线按默认端口生成，换端口后显示本机地址的截图不参与比对（见 support/scenarios.ts）。
const PORT = Number(process.env.E2E_PORT ?? DEFAULT_E2E_PORT);
const BASE_URL = `http://127.0.0.1:${PORT}`;
const OUTPUT_DIR = process.env.E2E_OUTPUT_DIR ?? "./test-results";

// 桌面验收视口：从最低支持的 1024×600 到验收上限 2560×1440。
const VIEWPORTS = [
  { width: 1024, height: 600 },
  { width: 1280, height: 600 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 2560, height: 1440 },
];

// 移动视口：iPhone SE 到主流安卓机。移动档的定义是视口窄于 768px（`(max-width: 47.999rem)`），
// 正好 768px 的竖屏平板落在桌面档，另由平板套件覆盖，避免用桌面布局去套移动端的断言。
const MOBILE_VIEWPORTS = [
  { name: "375x667", width: 375, height: 667 },
  { name: "390x844", width: 390, height: 844 },
  { name: "414x896", width: 414, height: 896 },
];

// 平板竖屏：移动档边界之上的最小宽度，验证桌面档在这个宽度下不溢出。
const TABLET_VIEWPORTS = [{ name: "768x1024", width: 768, height: 1024 }];

const isCI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  outputDir: OUTPUT_DIR,
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{testFilePath}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: 0,
  workers: isCI ? 1 : undefined,
  reporter: isCI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  expect: {
    toHaveScreenshot: { animations: "disabled", caret: "hide", scale: "css" },
  },
  use: {
    baseURL: BASE_URL,
    browserName: "chromium",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    colorScheme: "dark",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  projects: [
    // 溢出探针自测：合成页面不依赖视口与应用，只跑一次。
    {
      name: "layout-probe",
      testMatch: "probe/**/*.spec.ts",
      use: { viewport: { width: 1280, height: 720 } },
    },
    ...VIEWPORTS.map((viewport) => ({
      name: `${viewport.width}x${viewport.height}`,
      testMatch: "regions/**/*.spec.ts",
      use: { viewport },
    })),
    // 移动端套件：独立目录与独立视口，不参与桌面截图基线。
    ...MOBILE_VIEWPORTS.map((viewport) => ({
      name: `mobile-${viewport.name}`,
      testMatch: "mobile/**/*.spec.ts",
      testIgnore: "mobile/tablet.spec.ts",
      use: { viewport: { width: viewport.width, height: viewport.height }, hasTouch: true, isMobile: true },
    })),
    // 平板套件：同一目录的 tablet.spec.ts，跑在移动档边界之上的视口。
    ...TABLET_VIEWPORTS.map((viewport) => ({
      name: `tablet-${viewport.name}`,
      testMatch: "mobile/tablet.spec.ts",
      use: { viewport: { width: viewport.width, height: viewport.height }, hasTouch: true },
    })),
  ],
  webServer: {
    command: `pnpm exec vite build && pnpm exec vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !isCI,
    timeout: 180_000,
  },
});
