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

export const ENQUEUE_METHODS = [
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
  "startEpisodeReplan",
  "submitAssetSheetBatch",
  "submitStoryboardBatch",
];

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

const VIEWPORT_BREAKPOINT_ALLOWLIST = [
  "src/components/shared/page-shell/PageShell.tsx",
  "src/components/shared/page-shell/PageHeader.tsx",
  "src/components/shared/page-shell/PageSidebar.tsx",
];
const UI_PRIMITIVES = "src/components/ui/**";

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

const RESTRICT_VIEWPORT_BREAKPOINT = classGuards(
  String.raw`/(^|[\s:])(max-|min-)?(sm|md|lg|xl|2xl|\[[^\]]+\]):/`,
  "业务组件禁用视口断点前缀（sm: / md: / lg: / xl: / 2xl:）：外壳内一律用容器查询（@container 与 @md: 等），确需按视口切换的文件登记进 VIEWPORT_BREAKPOINT_ALLOWLIST。",
);

const RESTRICT_MOTION_VARIANT = classGuards(
  String.raw`/(^|[\s:])motion-(safe|reduce):/`,
  "禁用 motion-safe: / motion-reduce: 前缀：减少动态效果由 index.css 的全局规则统一处理，组件直接写动效类。",
);

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

const RESTRICT_SCROLLBAR_STYLE = classGuards(
  String.raw`/(^|[\s:\[])(no-scrollbar|scrollbar-(?!gutter)[\w-]+)|::-webkit-scrollbar/`,
  "禁止在组件里写滚动条样式（scrollbar-none、no-scrollbar、[scrollbar-width:*]、::-webkit-scrollbar 等）：滚动条始终可见，样式只在 index.css 定义。",
);

const RESTRICT_RAW_Z_INDEX = classGuards(
  String.raw`/(^|[\s:])-?z-(\d+|\[)/`,
  "调用处不写数值层级（z-10、z-[…]）：弹层、提示、引导分别由原语自带的 z-overlay、z-toast、z-onboarding 负责；#app-root 是独立层叠上下文（isolation: isolate），挂在 body 上的 Portal 总在应用之上。应用内吸顶的工具栏用 z-sticky。",
);

const RESTRICT_BACKDROP_BLUR = classGuards(
  String.raw`/(^|[\s:])backdrop-blur(?![\w-]*-0\b)/`,
  "不使用背景模糊（backdrop-blur-*）：弹层遮罩用 bg-scrim，表面用 bg-popover 加 shadow-overlay。半透明表面的文字对比度会随背后画面变化，背景模糊在大面积重绘时开销也大。",
);

const RESTRICT_RAW_MOTION_TOKEN = classGuards(
  String.raw`/(^|[\s:])(duration-\d+|ease-(linear|in|out|in-out))(?![\w-])/`,
  "时长与缓动只用 token：duration-fast（150ms）、duration-base（200ms）、ease-emphasized。动效不超过 300ms；弹层进出场用原语自带的 animate-in、animate-out，持续运行的状态指示用 animate-breath。",
);

const SOURCE_CLASS_GUARDS = [
  ...RESTRICT_MOTION_VARIANT,
  ...RESTRICT_UNPOSITIONED_SCROLL,
  ...RESTRICT_SCROLLBAR_STYLE,
  ...RESTRICT_RAW_Z_INDEX,
  ...RESTRICT_BACKDROP_BLUR,
  ...RESTRICT_RAW_MOTION_TOKEN,
];

const RESTRICT_RAW_TEXTAREA = {
  selector: "JSXOpeningElement[name.name='textarea']",
  message:
    "多行输入用 components/ui/textarea 的 Textarea：随内容撑高、超过上限后框内滚动、variant 与 mono 都由原语提供。必须使用原生 textarea 的场景（如与着色镜像层同步滚动的编辑器），行内 eslint-disable-next-line 并写明理由。",
};

const RESTRICT_TOAST_PRIMITIVE = {
  selector: String.raw`:matches(ImportDeclaration, ExportNamedDeclaration, ExportAllDeclaration)[source.value=/(^|\/)ui\/toast$/]`,
  message:
    "全局提示统一用 useAppStore 的 pushToast：ToastOverlay 负责把 store 中的提示按到达顺序转交 Base UI 队列，直接调用 toast.add 会绕过它，也绕过提示、工作区通知与持久告警的分流（见 src/stores/app-store.ts 中 pushToast 的说明）。",
};

const RESTRICT_UI_PRIMITIVE_IMPORTS = {
  patterns: [
    {
      group: ["@/api", "@/api/*", "@/stores/*", "@/types", "@/types/*", "@/hooks/*", "@/actions/*", "@/components/*", "!@/components/ui", "!@/components/ui/*"],
      message:
        "components/ui/ 只放 shadcn CLI 安装的原语：调用 API、读写 store 或依赖业务类型的组件放进使用它的业务目录，多处共用的放 components/shared/。knip 对 components/ui 的未使用导出整体豁免，业务组件混进来后，它真正未使用的导出也会被放过。",
    },
    {
      regex: String.raw`(^|/)\.\.(/|$)`,
      message: "components/ui/ 内跨目录导入用不含 .. 的 @/ 别名：带 .. 的路径绕过原语依赖边界的检查。",
    },
  ],
};

// @shadcn/lint 0.2.2 起把 render 元素上的 class 记到原语名下，按默认契约会误报「原语自带样式」，
// 故为这几个原语单列不限类别的契约。新增条目前先确认该原语在 components/ui 中确实不带样式。
const UNSTYLED_PRIMITIVES = ["Collapsible", "DialogTrigger", "InfoTipTrigger", "PopoverTrigger", "TooltipTrigger"];

const SHADCN_RULES = {
  "shadcn/no-restyle": [
    "error",
    {
      allow: ["layout"],
      contracts: [{ pattern: `^(${UNSTYLED_PRIMITIVES.join("|")})$`, allow: ["*"] }],
    },
  ],
  "shadcn/no-raw-colors": "error",
  "shadcn/no-arbitrary-values": ["error", { allow: ["layout"] }],
  "shadcn/no-inline-styles": "error",
  "shadcn/require-static-classes": "error",
  "shadcn/no-unknown-classes": "error",
};

const SOURCE_RESTRICTIONS = [
  RESTRICT_ENQUEUE,
  RESTRICT_CAPABILITIES,
  RESTRICT_MODULE_MOCK,
  RESTRICT_DISCARDED_REFRESH,
  ...RESTRICT_VIEWPORT_HEIGHT,
  ...RESTRICT_FIXED_OVERLAY,
  RESTRICT_SCROLL_HEIGHT,
  ...RESTRICT_VIEWPORT_BREAKPOINT,
  RESTRICT_RAW_TEXTAREA,
  RESTRICT_TOAST_PRIMITIVE,
  ...SOURCE_CLASS_GUARDS,
];
const restrictSyntax = (...exempt) => {
  const skipped = new Set(exempt.flat());
  return ["error", ...SOURCE_RESTRICTIONS.filter((rule) => !skipped.has(rule))];
};

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "playwright-report/**",
      "test-results/**",
      "coverage/**",
      "node_modules/**",
      "**/*.config.*",
    ],
  },

  js.configs.recommended,

  ...tseslint.configs.recommendedTypeChecked,

  {
    ...react.configs.flat.recommended,
    settings: { react: { version: "19" } },
  },
  react.configs.flat["jsx-runtime"],

  {
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },

  jsxA11y.flatConfigs.recommended,

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

  {
    files: ["e2e/regions/**/*.ts"],
    rules: {
      "no-restricted-syntax": ["error", {
        selector: "MemberExpression[property.name='getAnimations']",
        message: "页面级场景不手写动画等待：用 e2e/support/region-helpers.ts 的 waitForEntrance，或交给 defineRegionScenarios 探测前的统一等待。",
      }],
    },
  },

  {
    files: ["**/*.test.{ts,tsx}"],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    files: ["**/*.test.{ts,tsx}"],
    rules: Object.fromEntries(
      Object.keys(jsxA11y.flatConfigs.recommended.rules).map((rule) => [rule, "off"]),
    ),
  },

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

  {
    rules: {
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/incompatible-library": "error",
    },
  },

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
  {
    files: ["src/actions/**/*.{ts,tsx}"],
    ignores: TEST_FILES,
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_ENQUEUE) },
  },
  {
    files: ["src/hooks/useModelCapabilities.ts"],
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_CAPABILITIES) },
  },
  {
    files: ["src/components/layout/ToastOverlay.tsx"],
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_TOAST_PRIMITIVE) },
  },
  {
    files: [UI_PRIMITIVES],
    ignores: TEST_FILES,
    rules: {
      "shadcn/no-restyle": "off",
      "shadcn/no-arbitrary-values": "off",
      "shadcn/require-static-classes": "off",
      "no-restricted-syntax": restrictSyntax(
        RESTRICT_FIXED_OVERLAY,
        RESTRICT_SCROLL_HEIGHT,
        RESTRICT_VIEWPORT_BREAKPOINT,
        RESTRICT_RAW_TEXTAREA,
        RESTRICT_RAW_Z_INDEX,
        RESTRICT_RAW_MOTION_TOKEN,
      ),
      "no-restricted-imports": ["error", RESTRICT_UI_PRIMITIVE_IMPORTS],
    },
  },
  {
    files: VIEWPORT_BREAKPOINT_ALLOWLIST,
    rules: { "no-restricted-syntax": restrictSyntax(RESTRICT_VIEWPORT_BREAKPOINT) },
  },

  {
    files: TEST_FILES,
    plugins: { vitest },
    rules: {
      ...vitest.configs.recommended.rules,
      "vitest/expect-expect": ["error", { assertFunctionNames: ["expect", "expect*"] }],
    },
  },
  {
    files: TEST_FILES,
    ...testingLibrary.configs["flat/react"],
    rules: {
      ...testingLibrary.configs["flat/react"].rules,
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
