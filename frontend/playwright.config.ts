import { defineConfig } from "@playwright/test";
import { DEFAULT_E2E_PORT } from "./e2e/support/origin.ts";

// Page acceptance suites run CI official images matching @playwright/test (.github/workflows/test.yml
// frontend-e2e). Locally `pnpm e2e:server` starts identical-image run-server; `pnpm e2e:remote`
// connects for CI-equivalent rendering. Update both image versions alongside package.json @playwright/test.

// Environment overrides preview ports/output directories for parallel local suites; defaults match CI.
// Default-port screenshot baselines skip address-displaying comparisons on alternate ports (support/scenarios.ts).
const PORT = Number(process.env.E2E_PORT ?? DEFAULT_E2E_PORT);
const BASE_URL = `http://127.0.0.1:${PORT}`;
const OUTPUT_DIR = process.env.E2E_OUTPUT_DIR ?? "./test-results";

// Acceptance viewports span minimum 1024×600 through maximum 2560×1440.
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
    // Overflow-probe self-tests use viewport/app-independent synthetic pages, running once.
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
