import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
    clearMocks: true,
    coverage: {
      provider: "v8",
      all: true,
      include: [
        "src/api.ts",
        "src/stores/**/*.ts",
        "src/hooks/useTasksSSE.ts",
        "src/hooks/useScrollTarget.ts",
        "src/router.tsx",
        "src/components/pages/ProjectsPage.tsx",
        "src/components/canvas/StudioCanvasRouter.tsx",
      ],
      reporter: ["text", "json-summary", "lcov"],
      thresholds: {
        lines: 60,
      },
    },
  },
});
