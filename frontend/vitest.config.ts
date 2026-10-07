import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

const mockIconsPath = path.resolve(__dirname, "src/__mocks__/@lobehub/icons.tsx");

// 只收 .test.*：eslint 的测试规则块与 scripts/audit_tests.py 的前端发现都以此为界，
// 放开 vitest 默认的 .spec.* 会让这类文件被执行却不受两道闸门约束。
const TEST_FILES = "src/**/*.test.{ts,tsx}";

// 这两份用例改写 window.location、把 fetch 替身挂到全局：vmThreads 的 VM context 不允许
// 重定义 location，跨 realm 的全局替身也接不到调用。它们留在 forks 池，其余文件走 vmThreads。
const REALM_BOUND_TESTS = ["src/api.test.ts", "src/utils/sse-stream.test.ts"];

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
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
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
    clearMocks: true,
    // vmThreads 每个 worker 只建一次 jsdom，每个文件换一个 VM context；forks 每个文件新起进程
    // 并重建 jsdom。测试文件数以百计，按文件重建环境的固定开销压过了用例本身。
    projects: [
      {
        extends: true,
        test: {
          name: "vm",
          pool: "vmThreads",
          include: [TEST_FILES],
          exclude: [...configDefaults.exclude, ...REALM_BOUND_TESTS],
        },
      },
      {
        extends: true,
        test: {
          name: "realm-bound",
          pool: "forks",
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
