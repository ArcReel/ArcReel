// 与 website/eslint.config.mjs 的规则集同构但刻意不共用（理由见对侧头注释）；
// 改动共有规则时两份配置需各自同步维护。
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import vitest from "@vitest/eslint-plugin";
import testingLibrary from "eslint-plugin-testing-library";
import jestDom from "eslint-plugin-jest-dom";
import globals from "globals";
import { plugin as shadcn } from "@shadcn/lint";

const TEST_FILES = ["src/**/*.test.{ts,tsx}"];

// 入队类 API 方法清单：新增入队方法时在数组里加一行（按字母序）。
const ENQUEUE_METHODS = [
  "authorPrompts",
  "continueEpisodeReplan",
  "editImage",
  "exportJianyingDraft",
  "generateAdScript",
  "generateCharacter",
  "generateCharacterDerivative",
  "generateCharacterVoiceSample",
  "generateEpisodeNarrationAudio",
  "generateGrid",
  "generateNarrationAudio",
  "generateProjectProduct",
  "generateProjectProp",
  "generateProjectScene",
  "generateReferenceVideoBatch",
  "generateReferenceVideoUnit",
  "generateStoryboard",
  "generateVideo",
  "planEpisodes",
  "planScript",
  "regenerateGrid",
  "renderFinalCut",
  "repairEpisodeDraft",
  "startEpisodeReplan",
  "submitStoryboardBatch",
];

// no-restricted-syntax 的各条约束定义在此处、由下方配置块组合。
// flat config 对同一文件匹配到的同名规则是「后者整体替换前者的选项」而非合并：若拆成多个
// 配置块各写一条 selector，文件范围重叠时先声明的那条会被静默摘除。故每个配置块都必须把
// 该文件应受的全部约束一次性列全，豁免用「少列一条」表达，而不是另起一块。
const RESTRICT_ENQUEUE = {
  selector: `CallExpression[callee.object.name='API'][callee.property.name=/^(${ENQUEUE_METHODS.join("|")})$/]`,
  message:
    "入队类 API 方法只能经 src/actions/ 的入队动作层调用（统一封装乐观占用打标与去重提示）。",
};

const RESTRICT_CAPABILITIES = {
  selector:
    "CallExpression[callee.object.name='API'][callee.property.name='getVideoCapabilities']",
  message: "模型能力只能经 useModelCapabilities 消费（单一真相源 + 统一失效时机）。",
};

const RESTRICT_MODULE_MOCK = {
  selector:
    "CallExpression[callee.object.name='vi'][callee.property.name='mock'][arguments.0.value=/^(@\\/api(\\/.+)?|react-i18next)$/]",
  message:
    "禁止整模块 mock：API 打桩用 vi.spyOn(API, method)；i18n 用全局 setup 已加载的真实中文资源（整体 mock 后翻译缺失无法被发现）。",
};

// projects-store 的 refreshProject 以结算值报告失败而不 reject，丢弃返回值就没人提示刷新失败：
// 写入已生效，界面却停在旧数据上。写入后的刷新经 canvas/shared/refreshAfterWrite；
// 加载与同步路径传 onError，或消费结算值。
const REFRESH_PROJECT_CALL =
  "CallExpression[callee.property.name='refreshProject']:not(:has(Property[key.name='onError']))";
const RESTRICT_DISCARDED_REFRESH = {
  selector: [
    `ExpressionStatement > ${REFRESH_PROJECT_CALL}`,
    `ExpressionStatement > AwaitExpression > ${REFRESH_PROJECT_CALL}`,
    `UnaryExpression[operator='void'] > ${REFRESH_PROJECT_CALL}`,
  ].join(", "),
  message:
    "不要丢弃 refreshProject 的结算值：写入后的刷新改用 canvas/shared/refreshAfterWrite，加载与同步路径传 onError 或按结算值处理。",
};

// ---------------------------------------------------------------------------
// 样式与布局守卫：@shadcn/lint 与滚动、响应式守卫对全部源码生效，测试文件豁免；原语目录与下方白名单各放宽几条。
// 业务组件中确需按视口断点切换的文件（如外壳切换标准档与紧凑档、弹层宽度），逐个登记。
const VIEWPORT_BREAKPOINT_ALLOWLIST = [
  // 外壳按 xl 切换标准档与紧凑档：侧栏宽度、内容内边距与顶栏右侧留白
  "src/components/shared/page-shell/PageShell.tsx",
  "src/components/shared/page-shell/PageHeader.tsx",
  "src/components/shared/page-shell/PageSidebar.tsx",
];
const UI_PRIMITIVES = "src/components/ui/**";

// 字符串字面量与模板片段里的 class 守卫；先匹配含该 token 的字符串，再由 message 说明替代写法。
const classGuards = (pattern, message) => [
  { selector: `Literal[value=${pattern}]`, message },
  { selector: `TemplateElement[value.raw=${pattern}]`, message },
];

const RESTRICT_VIEWPORT_HEIGHT = classGuards(
  String.raw`/(^|[\s:])(min-|max-)?h-screen(?![\w-])|100vh/`,
  "禁止视口高度（h-screen、min-h-screen、max-h-screen、100vh）：高度由外壳的滚动契约分配，组件用 flex / grid 与 min-h-0 取得剩余空间。",
);

const RESTRICT_FIXED_OVERLAY = [
  {
    selector: String.raw`Literal[value=/(^|\s)fixed(\s|$)/][value=/(^|\s)inset-0(\s|$)/]`,
    message: "禁止手写 fixed inset-0 浮层：改用 components/ui 的 Dialog、Sheet 等原语。",
  },
  {
    selector: String.raw`TemplateElement[value.raw=/(^|\s)fixed(\s|$)/][value.raw=/(^|\s)inset-0(\s|$)/]`,
    message: "禁止手写 fixed inset-0 浮层：改用 components/ui 的 Dialog、Sheet 等原语。",
  },
];

const RESTRICT_SCROLL_HEIGHT = {
  selector: "MemberExpression[property.name='scrollHeight']",
  message: "禁止读写 scrollHeight 手动测高：自动撑高用统一的输入框原语，滚动区高度交给布局分配。",
};

// 容器查询变体以 @ 开头（@md:），不在此列。
const RESTRICT_VIEWPORT_BREAKPOINT = classGuards(
  String.raw`/(^|[\s:])(max-|min-)?(sm|md|lg|xl|2xl|\[[^\]]+\]):/`,
  "业务组件禁用视口断点前缀（sm: / md: / lg: / xl: / 2xl:）：外壳内一律用容器查询（@container 与 @md: 等），确需按视口切换的文件登记进 VIEWPORT_BREAKPOINT_ALLOWLIST。",
);

// 以下三条类名守卫同样对全部源码生效；测试文件豁免，它们在断言里引用类名而不渲染。
// 减少动态效果由 index.css 的全局规则统一处理。
const RESTRICT_MOTION_VARIANT = classGuards(
  String.raw`/(^|[\s:])motion-(safe|reduce):/`,
  "禁用 motion-safe: / motion-reduce: 前缀：减少动态效果由 index.css 的全局规则统一处理，组件直接写动效类。",
);

// 滚动容器须是定位元素，且定位类与 overflow 类写在同一个字符串里（守卫按单个字符串判断）。
const SCROLL_CLASS = String.raw`/(^|[\s:])overflow-(x-|y-)?(auto|scroll)(?![\w-])/`;
const POSITIONED_CLASS = String.raw`/(^|[\s:])(relative|absolute|fixed|sticky)(?![\w-])/`;
const UNPOSITIONED_SCROLL_MESSAGE =
  "滚动容器（overflow-auto、overflow-*-auto、overflow-*-scroll）须是定位元素：在同一个字符串里写 relative（已是 absolute、fixed、sticky 的除外），否则绝对定位的子元素会撑出文档滚动。";
const RESTRICT_UNPOSITIONED_SCROLL = [
  {
    selector: `Literal[value=${SCROLL_CLASS}]:not([value=${POSITIONED_CLASS}])`,
    message: UNPOSITIONED_SCROLL_MESSAGE,
  },
  {
    selector: `TemplateElement[value.raw=${SCROLL_CLASS}]:not([value.raw=${POSITIONED_CLASS}])`,
    message: UNPOSITIONED_SCROLL_MESSAGE,
  },
];

// 滚动条样式只在 index.css 定义；scrollbar-gutter 预留槽位，不改样式，不在此列。
const RESTRICT_SCROLLBAR_STYLE = classGuards(
  String.raw`/(^|[\s:\[])(no-scrollbar|scrollbar-(?!gutter)[\w-]+)|::-webkit-scrollbar/`,
  "禁止在组件里写滚动条样式（scrollbar-none、no-scrollbar、[scrollbar-width:*]、::-webkit-scrollbar 等）：滚动条始终可见，样式只在 index.css 定义。",
);

const SOURCE_CLASS_GUARDS = [
  ...RESTRICT_MOTION_VARIANT,
  ...RESTRICT_UNPOSITIONED_SCROLL,
  ...RESTRICT_SCROLLBAR_STYLE,
];

// @shadcn/lint 规则：components/ui 内的原语自身负责样式，关闭 restyle、任意值与静态 class 三条（见下方原语块）。
const SHADCN_RULES = {
  "shadcn/no-restyle": ["error", { allow: ["layout"] }],
  "shadcn/no-raw-colors": "error",
  "shadcn/no-arbitrary-values": ["error", { allow: ["layout"] }],
  "shadcn/no-inline-styles": "error",
  "shadcn/require-static-classes": "error",
  "shadcn/no-unknown-classes": "error",
};

// 源码应受的全部 no-restricted-syntax 约束。豁免块用 restrictSyntax(要豁免的约束…) 少列几条，
// 其余照列（替换语义见文件上方说明）：原语放过 fixed inset-0、scrollHeight 与视口断点，
// 视口断点白名单放过视口断点，入队动作层与 useModelCapabilities 各放过自己实现的那条。
const SOURCE_RESTRICTIONS = [
  RESTRICT_ENQUEUE,
  RESTRICT_CAPABILITIES,
  RESTRICT_MODULE_MOCK,
  RESTRICT_DISCARDED_REFRESH,
  ...RESTRICT_VIEWPORT_HEIGHT,
  ...RESTRICT_FIXED_OVERLAY,
  RESTRICT_SCROLL_HEIGHT,
  ...RESTRICT_VIEWPORT_BREAKPOINT,
  ...SOURCE_CLASS_GUARDS,
];
const restrictSyntax = (...exempt) => {
  const skipped = new Set(exempt.flat());
  return ["error", ...SOURCE_RESTRICTIONS.filter((rule) => !skipped.has(rule))];
};

export default tseslint.config(
  // 全局 ignores —— 覆盖 *.config.js 和 *.config.ts（vite.config.ts、vitest.config.ts）
  {
    ignores: [
      "dist/**",
      // Playwright 输出的报告与失败产物不属于项目源码。
      "playwright-report/**",
      "test-results/**",
      "coverage/**",
      "node_modules/**",
      "**/*.config.*",
    ],
  },

  // 通用 JS recommended
  js.configs.recommended,

  // TypeScript + typed linting（对所有 .ts/.tsx，后面在 src/** 里补 projectService）
  ...tseslint.configs.recommendedTypeChecked,

  // React 19
  {
    ...react.configs.flat.recommended,
    settings: { react: { version: "19" } },
  },
  react.configs.flat["jsx-runtime"],

  // React Hooks recommended
  {
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },

  // jsx-a11y recommended（非 strict）
  jsxA11y.flatConfigs.recommended,

  // 源码 typed linting 语言选项
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // 页面级套件与录制脚本：类型信息取自 e2e/tsconfig.json
  {
    files: ["e2e/**/*.ts"],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // 页面级场景不手写动画等待：Promise.all 等 finished 遇到被取消的过渡会拒绝，等全局动画会被循环动画卡住。
  // 公共入口已在探测前等待入场，场景中途需要等弹层时用 support/region-helpers 的 waitForEntrance。
  {
    files: ["e2e/regions/**/*.ts"],
    rules: {
      "no-restricted-syntax": ["error", {
        selector: "MemberExpression[property.name='getAnimations']",
        message: "页面级场景不手写动画等待：用 e2e/support/region-helpers.ts 的 waitForEntrance，或交给 defineRegionScenarios 探测前的统一等待。",
      }],
    },
  },

  // 测试文件：关闭 typed linting
  {
    files: ["**/*.test.{ts,tsx}"],
    ...tseslint.configs.disableTypeChecked,
  },
  // 测试文件：额外关闭所有 jsx-a11y rule（vitest/testing-library 用 a11y 反例做断言目标）
  {
    files: ["**/*.test.{ts,tsx}"],
    rules: Object.fromEntries(
      Object.keys(jsxA11y.flatConfigs.recommended.rules).map((rule) => [rule, "off"]),
    ),
  },

  // 测试文件放宽 any 与 unsafe-* —— 测试环境允许 mock 便利
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },

  // 项目惯例：_ 前缀变量/参数视为有意忽略，不报 unused-vars
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", {
        varsIgnorePattern: "^_",
        argsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
        destructuredArrayIgnorePattern: "^_",
      }],
    },
  },

  // 本项目严于 recommended：exhaustive-deps / incompatible-library 一律视为 error
  {
    rules: {
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/incompatible-library": "error",
    },
  },

  // API 直调约束。两条纪律：
  // - 入队类 API 方法只能经 src/actions/ 的入队动作层调用——乐观占用打标、去重提示与返回值
  //   归一化由动作层统一封装，组件直调会绕过这些副作用。新增入队类 API 方法时同步把方法名
  //   登记进 RESTRICT_ENQUEUE 的清单。
  // - 模型能力只能经 src/hooks/useModelCapabilities 消费——各能力维度的真相源、失效时机与
  //   「未知不谎报不支持」的降级规则都收在那里，组件直调会让目录侧与服务端侧重新分叉。
  // - 测试不得整模块 mock `@/api`（含其 `@/api/*` 子模块）与 `react-i18next`，该条对全部文件生效、无豁免。
  // - 不得丢弃 refreshProject 的结算值（RESTRICT_DISCARDED_REFRESH），该条同样对全部文件生效、无豁免。
  // src/api.test.ts 豁免前两条：它测试的是 API 层本体的端点路径与请求体。
  // 源码另受 @shadcn/lint 与滚动、响应式、类名守卫约束，测试文件不受（它们在断言里引用类名而不渲染）。
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: TEST_FILES,
    plugins: { shadcn },
    rules: {
      ...SHADCN_RULES,
      "no-restricted-syntax": restrictSyntax(),
    },
  },
  {
    files: TEST_FILES,
    ignores: ["src/api.test.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        RESTRICT_ENQUEUE,
        RESTRICT_CAPABILITIES,
        RESTRICT_MODULE_MOCK,
        RESTRICT_DISCARDED_REFRESH,
      ],
    },
  },
  {
    files: ["src/api.test.ts"],
    rules: {
      "no-restricted-syntax": ["error", RESTRICT_MODULE_MOCK, RESTRICT_DISCARDED_REFRESH],
    },
  },
  // 以下各块只放宽自己那几条，其余约束由 restrictSyntax 照列（见文件头对 flat config 替换语义的说明）。
  {
    files: ["src/actions/**/*.{ts,tsx}"],
    ignores: TEST_FILES,
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_ENQUEUE) },
  },
  {
    files: ["src/hooks/useModelCapabilities.ts"],
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_CAPABILITIES) },
  },
  // 原语自身负责样式与浮层定位：关闭 restyle、任意值与静态 class 三条，放过 fixed inset-0、scrollHeight 与视口断点。
  {
    files: [UI_PRIMITIVES],
    ignores: TEST_FILES,
    rules: {
      "shadcn/no-restyle": "off",
      "shadcn/no-arbitrary-values": "off",
      "shadcn/require-static-classes": "off",
      "no-restricted-syntax": restrictSyntax(RESTRICT_FIXED_OVERLAY, RESTRICT_SCROLL_HEIGHT, RESTRICT_VIEWPORT_BREAKPOINT),
    },
  },
  {
    files: VIEWPORT_BREAKPOINT_ALLOWLIST,
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_VIEWPORT_BREAKPOINT) },
  },

  // 测试三件套：vitest（`expect-expect` 管零断言）、testing-library、jest-dom。
  {
    files: TEST_FILES,
    plugins: { vitest },
    rules: {
      ...vitest.configs.recommended.rules,
      // `expectXxx(...)` 形态的本地断言辅助函数同样计作断言。
      "vitest/expect-expect": ["error", { assertFunctionNames: ["expect", "expect*"] }],
    },
  },
  {
    files: TEST_FILES,
    ...testingLibrary.configs["flat/react"],
    rules: {
      ...testingLibrary.configs["flat/react"].rules,
      // 存量命中且无自动修的查询/容器风格规则；断言强度归 review，不设禁令。
      "testing-library/no-node-access": "off",
      "testing-library/prefer-screen-queries": "off",
      "testing-library/no-container": "off",
      "testing-library/render-result-naming-convention": "off",
      "testing-library/no-unnecessary-act": "off",
      "testing-library/no-manual-cleanup": "off",
      // 以下三条的自动修在本仓产出语法错误或改写语义：prefer-find-by 改坏 waitFor 调用；
      // prefer-presence-queries 把喂给 `.closest()` 的 getBy 换成可空的 queryBy；
      // no-wait-for-multiple-assertions 把断言搬出 waitFor 回调时丢了作用域。
      "testing-library/prefer-find-by": "off",
      "testing-library/prefer-presence-queries": "off",
      "testing-library/no-wait-for-multiple-assertions": "off",
    },
  },
  {
    files: TEST_FILES,
    ...jestDom.configs["flat/recommended"],
    rules: {
      ...jestDom.configs["flat/recommended"].rules,
      // `toHaveAttribute("aria-valuenow", …)` 被判为表单取值断言，自动修会改写语义。
      "jest-dom/prefer-to-have-value": "off",
    },
  },
);
