import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

const mockIconsPath = path.resolve(__dirname, "src/__mocks__/@lobehub/icons.tsx");

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
    testTimeout: 15_000,
    coverage: {
      provider: "v8",
      all: true,
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/test/**", "src/__mocks__/**", "src/main.tsx", "src/vite-env.d.ts"],
      reporter: ["text", "json-summary", "lcov"],
      thresholds: {
        lines: 73,
      },
    },
  },
});
