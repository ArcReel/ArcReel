/// <reference types="node" />
import { ESLint } from "eslint";
import tseslint from "typescript-eslint";
import { beforeAll, describe, expect, it } from "vitest";

// Load actual eslint.config.js, disabling type information only: fixtures are outside TS projects and class guards need no types.
const eslint = new ESLint({
  overrideConfig: [{ files: ["**/*.{ts,tsx}"], ...tseslint.configs.disableTypeChecked }],
});

const GUARD_MESSAGE = {
  motion: "Do not use motion-safe: / motion-reduce: prefixes",
  scroll: "Scroll containers (overflow-auto",
  scrollbar: "Do not write scrollbar styles in components",
};

async function guardReports(code: string, filePath: string) {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages
    .filter((m) => m.ruleId === "no-restricted-syntax")
    .map((m) => m.message);
}

// Test string literals/template fragments separately without JSX so .ts fixtures also parse.
const literalFixture = (className: string) => `export const cls = ${JSON.stringify(className)};\n`;
const templateFixture = (className: string) =>
  `export const cls = (extra: string) => \`${className} \${extra}\`;\n`;

// Cover every no-restricted-syntax config block; flat config replaces whole same-name rule options, so omitted blocks silently lose guards.
const SOURCE_FILES = [
  "src/lint-fixture/Fixture.tsx", // Arbitrary source.
  "src/components/ui/fixture.tsx", // Primitive directories.
  "src/components/shared/page-shell/PageShell.tsx", // Viewport-breakpoint allowlists.
  "src/actions/fixture.tsx", // Enqueue action layers.
  "src/hooks/useModelCapabilities.ts", // Model-capability source of truth.
  "src/components/layout/ToastOverlay.tsx", // Sole toast-queue handoff point.
];

const LAYOUT_GUARDS = {
  viewportHeight: { code: literalFixture("min-h-0 h-screen"), message: "Do not use viewport heights" },
  fixedOverlay: { code: literalFixture("fixed inset-0 bg-scrim"), message: "Do not handwrite fixed inset-0 overlays" },
  scrollHeight: {
    code: "export const height = (el: HTMLElement) => el.scrollHeight;\n",
    message: "Do not read or write scrollHeight",
  },
  breakpoint: { code: literalFixture("flex md:grid"), message: "Business components must not use viewport breakpoint prefixes" },
  rawZIndex: { code: literalFixture("relative z-10"), message: "Do not write numeric stacking levels at call sites" },
  arbitraryZIndex: { code: literalFixture("hover:z-[60]"), message: "Do not write numeric stacking levels at call sites" },
  backdropBlur: { code: literalFixture("bg-popover/80 backdrop-blur-sm"), message: "Do not use backdrop blur" },
  rawDuration: { code: literalFixture("transition-colors duration-150"), message: "Use tokens only for duration and easing" },
  rawEase: { code: literalFixture("transition-opacity ease-in-out"), message: "Use tokens only for duration and easing" },
  toastPrimitive: {
    code: `import { toast } from "@/components/ui/toast";\nexport const t = toast;\n`,
    message: "Use useAppStore pushToast for global notices",
  },
  toastPrimitiveRelative: {
    code: `import { toast } from "../ui/toast";\nexport const t = toast;\n`,
    message: "Use useAppStore pushToast for global notices",
  },
  toastPrimitiveReexport: {
    code: `export { toast } from "@/components/ui/toast";\n`,
    message: "Use useAppStore pushToast for global notices",
  },
};
type LayoutGuard = keyof typeof LAYOUT_GUARDS;
const ALL_LAYOUT_GUARDS = Object.keys(LAYOUT_GUARDS) as LayoutGuard[];

const except = (...skipped: LayoutGuard[]) => ALL_LAYOUT_GUARDS.filter((g) => !skipped.includes(g));

// Layout guards cover all source; primitives allow fixed inset-0,scrollHeight,viewport breakpoints,numeric layers/raw-motion tokens, never background blur;
// allowlists permit viewport breakpoints; ToastOverlay permits toast-primitive imports.
const LAYOUT_ENFORCED: Record<string, LayoutGuard[]> = {
  "src/lint-fixture/Fixture.tsx": ALL_LAYOUT_GUARDS,
  "src/components/ui/fixture.tsx": [
    "viewportHeight",
    "backdropBlur",
    "toastPrimitive",
    "toastPrimitiveRelative",
    "toastPrimitiveReexport",
  ],
  "src/components/shared/page-shell/PageShell.tsx": except("breakpoint"),
  "src/actions/fixture.tsx": ALL_LAYOUT_GUARDS,
  "src/hooks/useModelCapabilities.ts": ALL_LAYOUT_GUARDS,
  "src/components/layout/ToastOverlay.tsx": except("toastPrimitive", "toastPrimitiveRelative", "toastPrimitiveReexport"),
};

const VIOLATIONS: Array<[keyof typeof GUARD_MESSAGE, string]> = [
  ["motion", "size-4 motion-safe:animate-spin"],
  ["motion", "motion-reduce:transition-none"],
  ["motion", "hover:motion-safe:scale-105"],
  ["scroll", "min-h-0 flex-1 overflow-y-auto"],
  ["scroll", "overflow-auto"],
  ["scroll", "flex overflow-x-scroll"],
  ["scroll", "@md/canvas:overflow-y-auto"],
  ["scrollbar", "relative overflow-x-auto scrollbar-none"],
  ["scrollbar", "relative overflow-y-auto no-scrollbar"],
  ["scrollbar", "relative overflow-y-auto [scrollbar-width:none]"],
  ["scrollbar", "relative overflow-y-auto [&::-webkit-scrollbar]:hidden"],
  ["scrollbar", "relative overflow-y-auto scrollbar-thin"],
];

const ALLOWED = [
  "size-4 animate-spin",
  "relative min-h-0 flex-1 overflow-y-auto",
  "absolute inset-x-0 overflow-auto",
  "sticky top-0 max-h-48 overflow-y-auto",
  "overflow-hidden",
  "overflow-x-hidden",
  "relative overflow-y-auto [scrollbar-gutter:stable]",
  "relative overflow-y-auto scrollbar-gutter-stable",
  "motion-path-fixture",
  "relative z-sticky",
  "transition-colors duration-fast ease-emphasized",
  "backdrop-blur-0",
];

describe("类名守卫", () => {
  // Initial config computation loads all plugins; warm separately, outside individual test timeouts.
  beforeAll(async () => {
    await eslint.calculateConfigForFile("src/lint-fixture/Fixture.tsx");
  }, 60_000);

  describe.each(SOURCE_FILES)("%s", (filePath) => {
    it.each(VIOLATIONS)("报出 %s 违规：%s", async (guard, className) => {
      // Use toContainEqual because arbitrary variants ([&::-webkit-scrollbar]:) also trigger viewport-breakpoint guards.
      expect(await guardReports(literalFixture(className), filePath)).toContainEqual(
        expect.stringContaining(GUARD_MESSAGE[guard]),
      );
      expect(await guardReports(templateFixture(className), filePath)).toContainEqual(
        expect.stringContaining(GUARD_MESSAGE[guard]),
      );
    });

    // Each block lists all replacement-semantic constraints; class guards must never displace existing same-block rules.
    it("类名守卫与丢弃刷新结算值的约束并存", async () => {
      const code = `declare const store: { refreshProject(): Promise<void> };\nstore.refreshProject();\n`;
      expect(await guardReports(code, filePath)).toEqual([
        expect.stringContaining("Do not discard refreshProject settlement results"),
      ]);
    });

    it.each(ALLOWED)("放过 %s", async (className) => {
      expect(await guardReports(literalFixture(className), filePath)).toEqual([]);
      expect(await guardReports(templateFixture(className), filePath)).toEqual([]);
    });
  });

  describe.each(SOURCE_FILES)("%s 的布局守卫与 @shadcn/lint", (filePath) => {
    it.each(ALL_LAYOUT_GUARDS)("%s 按所在配置块生效或放宽", async (guard) => {
      const { code, message } = LAYOUT_GUARDS[guard];
      const reported = (await guardReports(code, filePath)).some((m) => m.includes(message));
      expect(reported).toBe(LAYOUT_ENFORCED[filePath].includes(guard));
    });

    it("报出原始调色板", async () => {
      const code = `import { cn } from "cn";\nexport const cls = cn("bg-red-500");\n`;
      const [result] = await eslint.lintText(code, { filePath });
      expect(result.messages.map((m) => m.ruleId)).toContain("shadcn/no-raw-colors");
    });
  });

  it("原生 textarea 只在原语目录放行", async () => {
    const code = "export const Editor = () => <textarea aria-label=\"x\" />;\n";
    expect(await guardReports(code, "src/lint-fixture/Fixture.tsx")).toEqual([
      expect.stringContaining("Use components/ui/textarea Textarea"),
    ]);
    expect(await guardReports(code, "src/components/ui/fixture.tsx")).toEqual([]);
  });

  it("原语目录不得依赖业务模块", async () => {
    const code = [
      `import { API } from "@/api";`,
      `import { useAppStore } from "@/stores/app-store";`,
      `import { PageShell } from "@/components/shared/page-shell/PageShell";`,
      `import { Button } from "@/components/ui/button";`,
      `import { useTranslation } from "react-i18next";`,
      `import { Dialog } from "./dialog";`,
      `import { useConfigStore } from "../../stores/config-store";`,
      `import { DetailPane } from "../shared/master-detail/DetailPane";`,
      `import { useProjectsStore } from "./../../stores/projects-store";`,
      `import { useTasksStore } from "@/components/ui/../../stores/tasks-store";`,
      `export const all = [API, useAppStore, PageShell, Button, useTranslation, Dialog, useConfigStore, DetailPane, useProjectsStore, useTasksStore];`,
      "",
    ].join("\n");
    const [result] = await eslint.lintText(code, { filePath: "src/components/ui/fixture.tsx" });
    const restricted = result.messages.filter((m) => m.ruleId === "no-restricted-imports").map((m) => m.line);
    expect(restricted).toEqual([1, 2, 3, 7, 8, 9, 10]);
  });

  it("测试文件不受布局守卫与 @shadcn/lint 约束", async () => {
    const code = `import { cn } from "cn";\nit("x", () => { expect(cn("bg-red-500 h-screen fixed inset-0 md:grid")).toBeTruthy(); });\n`;
    const [result] = await eslint.lintText(code, { filePath: "src/lint-fixture/Fixture.test.tsx" });
    expect(result.messages.filter((m) => m.ruleId === "no-restricted-syntax" || m.ruleId?.startsWith("shadcn/"))).toEqual([]);
  });

  it("测试文件不受类名守卫约束", async () => {
    const code = `it("x", () => { expect(el).toHaveClass("overflow-y-auto motion-safe:animate-spin"); });\n`;
    expect(await guardReports(code, "src/lint-fixture/Fixture.test.tsx")).toEqual([]);
  });

  it("补过 relative 的滚动容器零报告", async () => {
    const results = await eslint.lintFiles([
      "src/components/pages/settings/PromptTemplateDetailView.tsx",
      "src/components/pages/settings/endpoints/ComfyuiEndpointDetail.tsx",
      "src/components/pages/settings/promptTemplateShared.tsx",
      "src/components/ui/attachment.tsx",
      "src/components/ui/combobox.tsx",
      "src/components/ui/dropdown-menu.tsx",
      "src/components/ui/popover.tsx",
      "src/components/ui/textarea.tsx",
      "src/components/ui/toast.tsx",
    ]);
    const reports = results.flatMap((r) =>
      r.messages
        .filter((m) => m.ruleId === "no-restricted-syntax")
        .map((m) => `${r.filePath}:${m.line} ${m.message}`),
    );
    expect(reports).toEqual([]);
  }, 60_000);
});
