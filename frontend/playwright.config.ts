import { defineConfig } from "@playwright/test";
import { DEFAULT_E2E_PORT } from "./e2e/support/origin.ts";
import { projectName, selectedViewports } from "./e2e/support/viewports.ts";

// 截图基线按默认端口生成，换端口后显示本机地址的截图不参与比对（见 support/scenarios.ts）。
const live = process.env.E2E_LIVE === "1";
const PORT = Number(process.env.E2E_PORT ?? (live ? 4174 : DEFAULT_E2E_PORT));
const BASE_URL = `http://127.0.0.1:${PORT}`;
const OUTPUT_DIR = process.env.E2E_OUTPUT_DIR ?? "./test-results";

const isCI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  outputDir: OUTPUT_DIR,
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{testFilePath}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: 0,
  workers: live || isCI ? 1 : undefined,
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
  projects: live ? [{
    name: "live",
    testMatch: "live/**/*.spec.ts",
    use: { viewport: { width: 1440, height: 900 } },
  }] : [
    {
      name: "layout-probe",
      testMatch: "probe/**/*.spec.ts",
      use: { viewport: { width: 1280, height: 720 } },
    },
    ...selectedViewports().map((viewport) => ({
      name: projectName(viewport),
      testMatch: "regions/**/*.spec.ts",
      use: { viewport: { width: viewport.width, height: viewport.height } },
    })),
  ],
  webServer: {
    command: live
      ? `pnpm exec vite build && cd .. && uv run --frozen python -m frontend.e2e.live.backend --port ${PORT}`
      : `pnpm exec vite build && pnpm exec vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: live ? `${BASE_URL}/health` : BASE_URL,
    reuseExistingServer: !live && !isCI,
    gracefulShutdown: live ? { signal: "SIGTERM", timeout: 10_000 } : undefined,
    timeout: 180_000,
  },
});
