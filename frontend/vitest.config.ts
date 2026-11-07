import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

const mockIconsPath = path.resolve(__dirname, "src/__mocks__/@lobehub/icons.tsx");

// Collect .test.* only, matching ESLint test rules/scripts/audit_tests.py discovery;
// default .spec.* would execute outside both gates.
const TEST_FILES = "src/**/*.test.{ts,tsx}";

// Two cases rewrite window.location/install global fetch fakes. vmThreads VM contexts cannot
// redefine location or receive cross-realm fakes; use forks for these, vmThreads elsewhere.
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
    // vmThreads creates jsdom once per worker/new VM per file; forks recreates processes/jsdom
    // per file. Hundreds of test files make environment setup overhead exceed case execution.
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
