import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";
import {
  COMPACT_VIEWPORT,
  DESKTOP_VIEWPORT,
  projectName,
  RESPONSIVE_TAG,
  VIEWPORT_CONTRACT_TAG,
} from "./e2e/support/viewports.ts";

const live = process.env.E2E_LIVE === "1";
const PORT = Number(process.env.E2E_PORT ?? (live ? 4174 : 4173));
const BASE_URL = `http://127.0.0.1:${PORT}`;
const OUTPUT_DIR = process.env.E2E_OUTPUT_DIR ?? "./test-results";

const isCI = Boolean(process.env.CI);

const prebuilt = process.env.E2E_PREBUILT === "1";
if (prebuilt && !existsSync(new URL("./dist/index.html", import.meta.url))) {
  throw new Error("Shared browser build is missing frontend/dist/index.html");
}
const startServer = live
  ? `cd .. && uv run --frozen python -m frontend.e2e.live.backend --port ${PORT}`
  : `node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port ${PORT} --strictPort`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: OUTPUT_DIR,
  fullyParallel: true,
  forbidOnly: isCI,
  retries: 0,
  workers: live ? 1 : isCI ? 2 : undefined,
  reporter: isCI ? [["list"], ["html", { open: "never" }]] : [["list"]],
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
    use: { viewport: { width: DESKTOP_VIEWPORT.width, height: DESKTOP_VIEWPORT.height } },
  }] : [
    {
      name: "layout-probe",
      testMatch: "probe/**/*.spec.ts",
      use: { viewport: { width: 1280, height: 720 } },
    },
    {
      name: projectName(DESKTOP_VIEWPORT),
      testMatch: "regions/**/*.spec.ts",
      grepInvert: new RegExp(VIEWPORT_CONTRACT_TAG),
      use: { viewport: { width: DESKTOP_VIEWPORT.width, height: DESKTOP_VIEWPORT.height } },
    },
    {
      name: projectName(COMPACT_VIEWPORT),
      testMatch: "regions/**/*.spec.ts",
      grep: new RegExp(RESPONSIVE_TAG),
      grepInvert: new RegExp(VIEWPORT_CONTRACT_TAG),
      use: { viewport: { width: COMPACT_VIEWPORT.width, height: COMPACT_VIEWPORT.height } },
    },
    {
      name: "viewport-contracts",
      testMatch: "regions/**/*.spec.ts",
      grep: new RegExp(VIEWPORT_CONTRACT_TAG),
      use: { viewport: { width: DESKTOP_VIEWPORT.width, height: DESKTOP_VIEWPORT.height } },
    },
  ],
  webServer: {
    command: prebuilt ? startServer : `pnpm exec vite build && ${startServer}`,
    stdout: live && isCI ? "pipe" : "ignore",
    url: live ? `${BASE_URL}/health` : BASE_URL,
    reuseExistingServer: !prebuilt && !live && !isCI,
    gracefulShutdown: live ? { signal: "SIGTERM", timeout: 10_000 } : undefined,
    timeout: 180_000,
  },
});
