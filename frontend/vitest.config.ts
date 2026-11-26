import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

const mockIconsPath = path.resolve(__dirname, "src/__mocks__/@lobehub/icons.tsx");

const DOM_TESTS = [
  "src/**/*.test.tsx",
  "src/components/canvas/episodes/useMoveEpisode.test.ts",
  "src/components/canvas/lorebook/useAssetSheetStatus.test.ts",
  "src/components/pages/settings/endpoints/use-trial-run.test.ts",
  "src/components/pages/settings/useRowIds.test.ts",
  "src/components/usage/use-task-cancellation.test.ts",
  "src/onboarding/read-only-mode.test.ts",
  "src/onboarding/tour.test.ts",
  "src/stores/app-store.test.ts",
  "src/stores/tasks-store.test.ts",
  "src/theme.test.ts",
  "src/utils/download.test.ts",
  "src/utils/safe-url.test.ts",
];

// 这两份用例改写 window.location、把 fetch 替身挂到全局：vmThreads 的 VM context 不允许
// 重定义 location，跨 realm 的全局替身也接不到调用。它们留在 forks 池，其余文件走 vmThreads。
const REALM_BOUND_TESTS = ["src/api.test.ts", "src/utils/sse-stream.test.ts"];

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^@\/i18n$/, replacement: path.resolve(__dirname, "src/test/i18n.ts") },
      { find: "@", replacement: path.resolve(__dirname, "src") },
      // Mock @lobehub/icons and all its subpath imports to avoid
      // @lobehub/fluent-emoji ESM directory import errors in tests.
      {
        find: /^@lobehub\/icons(\/.*)?$/,
        replacement: mockIconsPath,
      },
    ],
  },
  test: {
    restoreMocks: true,
    clearMocks: true,
    // vmThreads 每个 worker 只建一次 jsdom，每个文件换一个 VM context；forks 每个文件新起进程
    // 并重建 jsdom。测试文件数以百计，按文件重建环境的固定开销压过了用例本身。
    projects: [
      {
        extends: true,
        test: {
          name: "node",
          environment: "node",
          setupFiles: ["src/test/setup-node.ts"],
          pool: "vmThreads",
          include: ["src/**/*.test.ts"],
          exclude: [...configDefaults.exclude, ...DOM_TESTS, ...REALM_BOUND_TESTS],
        },
      },
      {
        extends: true,
        test: {
          name: "vm",
          pool: "vmThreads",
          environment: "jsdom",
          setupFiles: ["src/test/setup.ts"],
          include: DOM_TESTS,
          exclude: [...configDefaults.exclude, ...REALM_BOUND_TESTS],
        },
      },
      {
        extends: true,
        test: {
          name: "realm-bound",
          pool: "forks",
          environment: "jsdom",
          setupFiles: ["src/test/setup.ts"],
          include: REALM_BOUND_TESTS,
        },
      },
    ],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/test/**", "src/__mocks__/**", "src/main.tsx", "src/vite-env.d.ts"],
      reporter: ["text", "json-summary", "lcov"],
    },
  },
});
