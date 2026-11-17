import { defineConfig } from "@playwright/test";
import { DEFAULT_E2E_PORT } from "./e2e/support/origin.ts";
import { projectName, selectedViewports } from "./e2e/support/viewports.ts";

// 页面级验收套件。CI 在与 @playwright/test 同版本的官方镜像里运行（.github/workflows/test.yml 的
// frontend-e2e job）；本地用 `pnpm e2e:server` 在同一镜像里起 run-server，再用 `pnpm e2e:remote`
// 连上去渲染，结果与 CI 一致。两处镜像版本都要跟 package.json 里的 @playwright/test 一起改。

// 预览端口与产物目录可用环境变量覆盖，供同一台机器上并行运行多份套件；不设置时与 CI 一致。
// 截图基线按默认端口生成，换端口后显示本机地址的截图不参与比对（见 support/scenarios.ts）。
const PORT = Number(process.env.E2E_PORT ?? DEFAULT_E2E_PORT);
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
    ...selectedViewports().map((viewport) => ({
      name: projectName(viewport),
      testMatch: "regions/**/*.spec.ts",
      use: { viewport: { width: viewport.width, height: viewport.height } },
    })),
  ],
  webServer: {
    command: `pnpm exec vite build && pnpm exec vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !isCI,
    timeout: 180_000,
  },
});
