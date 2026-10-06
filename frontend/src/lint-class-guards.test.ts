/// <reference types="node" />
import { ESLint } from "eslint";
import tseslint from "typescript-eslint";
import { beforeAll, describe, expect, it } from "vitest";

// 加载真实的 eslint.config.js，只关掉类型信息：夹具文件不在 TS 工程里，类名守卫也不需要类型。
const eslint = new ESLint({
  overrideConfig: [{ files: ["**/*.{ts,tsx}"], ...tseslint.configs.disableTypeChecked }],
});

const GUARD_MESSAGE = {
  motion: "禁用 motion-safe: / motion-reduce: 前缀",
  scroll: "滚动容器（overflow-auto",
  scrollbar: "禁止在组件里写滚动条样式",
};

async function guardReports(code: string, filePath: string) {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages
    .filter((m) => m.ruleId === "no-restricted-syntax")
    .map((m) => m.message);
}

// 字符串字面量与模板片段两种写法各测一遍；不用 JSX，.ts 夹具同样能解析。
const literalFixture = (className: string) => `export const cls = ${JSON.stringify(className)};\n`;
const templateFixture = (className: string) =>
  `export const cls = (extra: string) => \`${className} \${extra}\`;\n`;

// 覆盖每个带 no-restricted-syntax 的配置块：flat config 对同名规则整体替换选项，漏列一块就会静默失守。
const SOURCE_FILES = [
  "src/lint-fixture/Fixture.tsx", // 任意源码
  "src/components/ui/fixture.tsx", // 原语目录
  "src/components/shared/page-shell/PageShell.tsx", // 视口断点白名单
  "src/actions/fixture.tsx", // 入队动作层
  "src/hooks/useModelCapabilities.ts", // 模型能力真相源
];

const LAYOUT_GUARDS = {
  viewportHeight: { code: literalFixture("min-h-0 h-screen"), message: "禁止视口高度" },
  fixedOverlay: { code: literalFixture("fixed inset-0 bg-scrim"), message: "禁止手写 fixed inset-0 浮层" },
  scrollHeight: {
    code: "export const height = (el: HTMLElement) => el.scrollHeight;\n",
    message: "禁止读写 scrollHeight",
  },
  breakpoint: { code: literalFixture("flex md:grid"), message: "业务组件禁用视口断点前缀" },
};
type LayoutGuard = keyof typeof LAYOUT_GUARDS;
const ALL_LAYOUT_GUARDS = Object.keys(LAYOUT_GUARDS) as LayoutGuard[];

// 布局守卫对全部源码生效：原语放过 fixed inset-0、scrollHeight 与视口断点，白名单放过视口断点。
const LAYOUT_ENFORCED: Record<string, LayoutGuard[]> = {
  "src/lint-fixture/Fixture.tsx": ALL_LAYOUT_GUARDS,
  "src/components/ui/fixture.tsx": ["viewportHeight"],
  "src/components/shared/page-shell/PageShell.tsx": ["viewportHeight", "fixedOverlay", "scrollHeight"],
  "src/actions/fixture.tsx": ALL_LAYOUT_GUARDS,
  "src/hooks/useModelCapabilities.ts": ALL_LAYOUT_GUARDS,
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
];

describe("类名守卫", () => {
  // 首次计算配置要加载全部插件，单独预热，不计入各条用例的超时。
  beforeAll(async () => {
    await eslint.calculateConfigForFile("src/lint-fixture/Fixture.tsx");
  }, 60_000);

  describe.each(SOURCE_FILES)("%s", (filePath) => {
    it.each(VIOLATIONS)("报出 %s 违规：%s", async (guard, className) => {
      // 用 toContainEqual：任意值变体（[&::-webkit-scrollbar]:）还会被视口断点守卫报出。
      expect(await guardReports(literalFixture(className), filePath)).toContainEqual(
        expect.stringContaining(GUARD_MESSAGE[guard]),
      );
      expect(await guardReports(templateFixture(className), filePath)).toContainEqual(
        expect.stringContaining(GUARD_MESSAGE[guard]),
      );
    });

    // 各块按替换语义列全约束：加入类名守卫时不得挤掉同块原有的约束。
    it("类名守卫与丢弃刷新结算值的约束并存", async () => {
      const code = `declare const store: { refreshProject(): Promise<void> };\nstore.refreshProject();\n`;
      expect(await guardReports(code, filePath)).toEqual([
        expect.stringContaining("不要丢弃 refreshProject 的结算值"),
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
