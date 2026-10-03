import { defineConfig } from "@playwright/test";

// 页面级验收套件。CI 在与 @playwright/test 同版本的官方镜像里运行（.github/workflows/test.yml 的
// frontend-e2e job）；本地用 `pnpm e2e:server` 在同一镜像里起 run-server，再用 `pnpm e2e:remote`
// 连上去渲染，结果与 CI 一致。两处镜像版本都要跟 package.json 里的 @playwright/test 一起改。

const PORT = 4173;
const BASE_URL = `http://127.0.0.1:${PORT}`;

// 验收视口：从最低支持的 1024×600 到验收上限 2560×1440。
const VIEWPORTS = [
  { width: 1024, height: 600 },
  { width: 1280, height: 600 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 2560, height: 1440 },
];

const isCI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
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
  ],
  webServer: {
    command: `pnpm exec vite build && pnpm exec vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !isCI,
    timeout: 180_000,
  },
});
