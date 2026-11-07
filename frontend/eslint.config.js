// Same rule structure as website/eslint.config.mjs, deliberately separate for reasons in its header;
// maintain shared rules in both configurations independently.
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

// Alphabetized enqueue APIs: add JSDoc @enqueue in api.ts plus entries here for new methods;
// api.enqueue-methods.test.ts enforces identical sets, failing missing registrations.
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
  "repairEpisodeDraft",
  "startEpisodeReplan",
  "submitAssetSheetBatch",
  "submitStoryboardBatch",
];

// Define no-restricted-syntax constraints here, composed by configuration blocks below.
// Flat config wholly replaces earlier same-named rule options for matching files, never merges. Separate
// overlapping selector blocks silently remove earlier guards; every block must list
// all applicable constraints together. Exemptions omit constraints rather than add blocks.
const RESTRICT_ENQUEUE = {
  selector: `CallExpression[callee.object.name='API'][callee.property.name=/^(${ENQUEUE_METHODS.join("|")})$/]`,
  message:
    "Enqueue API methods must be called only through the src/actions/ enqueue action layer (centralizing optimistic occupancy markers and deduplication notices).",
};

const RESTRICT_CAPABILITIES = {
  selector:
    "CallExpression[callee.object.name='API'][callee.property.name='getVideoCapabilities']",
  message: "Model capabilities must be consumed only through useModelCapabilities (one source of truth and coordinated invalidation).",
};

const RESTRICT_MODULE_MOCK = {
  selector:
    "CallExpression[callee.object.name='vi'][callee.property.name='mock'][arguments.0.value=/^(@\\/api(\\/.+)?|react-i18next)$/]",
  message:
    "Do not mock entire modules: stub API calls with vi.spyOn(API, method); use the real Chinese i18n resources loaded by global setup (whole-module mocks hide missing translations).",
};

// projects-store refreshProject resolves failure results without rejection; discarded results lose failure notices,
// leaving UI stale after successful writes. Postwrite refreshes use canvas/shared/refreshAfterWrite;
// loading/synchronization pass onError or consume settlement results.
const REFRESH_PROJECT_CALL =
  "CallExpression[callee.property.name='refreshProject']:not(:has(Property[key.name='onError']))";
const RESTRICT_DISCARDED_REFRESH = {
  selector: [
    `ExpressionStatement > ${REFRESH_PROJECT_CALL}`,
    `ExpressionStatement > AwaitExpression > ${REFRESH_PROJECT_CALL}`,
    `UnaryExpression[operator='void'] > ${REFRESH_PROJECT_CALL}`,
  ].join(", "),
  message:
    "Do not discard refreshProject settlement results: use canvas/shared/refreshAfterWrite after writes; loading and synchronization paths must pass onError or handle settlement results.",
};

// ---------------------------------------------------------------------------
// Style/layout guards apply @shadcn/lint, scrolling/responsiveness to all source, excluding tests; primitives/allowlists relax selected rules.
// Register individual business files genuinely requiring viewport breakpoints, such as shell modes/overlay widths.
const VIEWPORT_BREAKPOINT_ALLOWLIST = [
  // Shell xl standard/compact switches control sidebar widths/content padding/header-right spacing.
  "src/components/shared/page-shell/PageShell.tsx",
  "src/components/shared/page-shell/PageHeader.tsx",
  "src/components/shared/page-shell/PageSidebar.tsx",
];
const UI_PRIMITIVES = "src/components/ui/**";

// Class guards inspect strings/template fragments, matching tokens before messages explain replacements.
const classGuards = (pattern, message) => [
  { selector: `Literal[value=${pattern}]`, message },
  { selector: `TemplateElement[value.raw=${pattern}]`, message },
];

const RESTRICT_VIEWPORT_HEIGHT = classGuards(
  String.raw`/(^|[\s:])(min-|max-)?h-screen(?![\w-])|100vh/`,
  "Do not use viewport heights (h-screen, min-h-screen, max-h-screen, 100vh): the shell scrolling contract allocates height; components use flex / grid and min-h-0 for remaining space.",
);

const RESTRICT_FIXED_OVERLAY = [
  {
    selector: String.raw`Literal[value=/(^|\s)fixed(\s|$)/][value=/(^|\s)inset-0(\s|$)/]`,
    message: "Do not handwrite fixed inset-0 overlays: use primitives such as Dialog and Sheet from components/ui/.",
  },
  {
    selector: String.raw`TemplateElement[value.raw=/(^|\s)fixed(\s|$)/][value.raw=/(^|\s)inset-0(\s|$)/]`,
    message: "Do not handwrite fixed inset-0 overlays: use primitives such as Dialog and Sheet from components/ui/.",
  },
];

const RESTRICT_SCROLL_HEIGHT = {
  selector: "MemberExpression[property.name='scrollHeight']",
  message: "Do not read or write scrollHeight for manual height measurement: use shared input primitives for automatic growth and layout allocation for scroll-region height.",
};

// Container variants start @, such as @md:, excluded here.
const RESTRICT_VIEWPORT_BREAKPOINT = classGuards(
  String.raw`/(^|[\s:])(max-|min-)?(sm|md|lg|xl|2xl|\[[^\]]+\]):/`,
  "Business components must not use viewport breakpoint prefixes (sm: / md: / lg: / xl: / 2xl:): use container queries (@container, @md:, etc.) inside shells; register files genuinely requiring viewport switches in VIEWPORT_BREAKPOINT_ALLOWLIST.",
);

// Three class guards below apply all source, excluding tests referencing class names without rendering.
// index.css global rules handle reduced motion centrally.
const RESTRICT_MOTION_VARIANT = classGuards(
  String.raw`/(^|[\s:])motion-(safe|reduce):/`,
  "Do not use motion-safe: / motion-reduce: prefixes: global index.css rules handle reduced motion centrally; components write animation classes directly.",
);

// Scroll containers require positioning and overflow classes in the same string; guards evaluate per string.
const SCROLL_CLASS = String.raw`/(^|[\s:])overflow-(x-|y-)?(auto|scroll)(?![\w-])/`;
const POSITIONED_CLASS = String.raw`/(^|[\s:])(relative|absolute|fixed|sticky)(?![\w-])/`;
const UNPOSITIONED_SCROLL_MESSAGE =
  "Scroll containers (overflow-auto, overflow-*-auto, overflow-*-scroll) must be positioned: include relative in the same string (unless already absolute, fixed, or sticky), or absolutely positioned children can expand document scrolling.";
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

// Scrollbar styles live only in index.css; scrollbar-gutter reserves space without styling, excluded.
const RESTRICT_SCROLLBAR_STYLE = classGuards(
  String.raw`/(^|[\s:\[])(no-scrollbar|scrollbar-(?!gutter)[\w-]+)|::-webkit-scrollbar/`,
  "Do not write scrollbar styles in components (scrollbar-none, no-scrollbar, [scrollbar-width:*], ::-webkit-scrollbar, etc.): scrollbars remain visible and styles are defined only in index.css.",
);

// Primitives own z-overlay/z-toast/z-onboarding layers; primitive directories exempt.
const RESTRICT_RAW_Z_INDEX = classGuards(
  String.raw`/(^|[\s:])-?z-(\d+|\[)/`,
  "Do not write numeric stacking levels at call sites (z-10, z-[…]): primitives own z-overlay/z-toast/z-onboarding for overlays, toasts, and onboarding respectively; #app-root has an independent stacking context (isolation: isolate), so body-mounted Portals stay above the application. Use z-sticky for sticky in-app toolbars.",
);

// Applies all source, including primitives; backdrop-blur-0 removes blur and remains permitted.
const RESTRICT_BACKDROP_BLUR = classGuards(
  String.raw`/(^|[\s:])backdrop-blur(?![\w-]*-0\b)/`,
  "Do not use backdrop blur (backdrop-blur-*): overlays use bg-scrim; surfaces use bg-popover plus shadow-overlay. Text contrast on translucent surfaces varies with the picture behind it, and backdrop blur is expensive during large repaints.",
);

// Primitives exempt because their entrance/exit animations define these tokens.
const RESTRICT_RAW_MOTION_TOKEN = classGuards(
  String.raw`/(^|[\s:])(duration-\d+|ease-(linear|in|out|in-out))(?![\w-])/`,
  "Use tokens only for duration and easing: duration-fast (150ms), duration-base (200ms), ease-emphasized. Animations must not exceed 300ms; overlay entrance/exit uses primitive animate-in/animate-out, and continuously running status indicators use animate-breath.",
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
    "Use components/ui/textarea Textarea for multiline input: it grows with content, scrolls internally above its limit, and provides variant and mono. When native textarea is required (e.g. editors synchronizing scrolling with syntax-highlight mirrors), add an inline eslint-disable-next-line with a reason.",
};

const RESTRICT_TOAST_PRIMITIVE = {
  // Match imports/reexports, alias/relative paths, including @/components/ui/toast and ../ui/toast.
  selector: String.raw`:matches(ImportDeclaration, ExportNamedDeclaration, ExportAllDeclaration)[source.value=/(^|\/)ui\/toast$/]`,
  message:
    "Use useAppStore pushToast for global notices: ToastOverlay forwards store notices to the Base UI queue in arrival order. Direct toast.add bypasses this and the routing between toasts, workspace notifications, and persistent alerts (see pushToast in src/stores/app-store.ts).",
};

// components/ui/ contains shadcn primitives only, without API/store/business-type/component dependencies.
// Permit react-i18next for dialog/sheet/toast/message-scroller close-label copy.
const RESTRICT_UI_PRIMITIVE_IMPORTS = {
  patterns: [
    {
      group: ["@/api", "@/api/*", "@/stores/*", "@/types", "@/types/*", "@/hooks/*", "@/actions/*", "@/components/*", "!@/components/ui", "!@/components/ui/*"],
      message:
        "components/ui/ contains only shadcn CLI-installed primitives: components calling APIs, reading/writing stores, or depending on business types belong in their consuming business directories; shared ones belong in components/shared/. knip exempts unused exports throughout components/ui/, so misplaced business components would hide truly unused exports.",
    },
    // Primitive directories lack subdirectories; .. escapes components/ui, so require @/ aliases without .. for boundary detection.
    {
      regex: String.raw`(^|/)\.\.(/|$)`,
      message: "Use @/ aliases without .. for cross-directory imports within components/ui/: paths containing .. bypass primitive dependency-boundary checks.",
    },
  ],
};

// Style-free behavioral primitives: components/ui forwards props without className, except InfoTipTrigger focus-ring.
// Callers style rendered elements through render/className; see info-tip.tsx.
// @shadcn/lint 0.2.2 attributes render-element classes to primitives, falsely reporting built-in styles;
// give these primitives unrestricted category contracts. Verify actual style-free implementations before adding entries.
const UNSTYLED_PRIMITIVES = ["Collapsible", "DialogTrigger", "InfoTipTrigger", "PopoverTrigger", "TooltipTrigger"];

// @shadcn/lint primitives own styles, disabling restyle/arbitrary/static-class guards below.
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

// Source no-restricted-syntax sets list all constraints; exempt blocks use restrictSyntax to omit selected constraints
// while retaining others, following replacement semantics above. Primitives permit fixed inset-0, scrollHeight, viewport breakpoints, native textarea,
// numeric layers/raw motion; viewport allowlists permit viewport changes; action layers/useModelCapabilities/ToastOverlay
// each exempt only their implemented responsibility.
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
  // Global ignores cover *.config.js/*.config.ts, including vite.config.ts/vitest.config.ts.
  {
    ignores: [
      "dist/**",
      // Playwright reports/failure artifacts are not source.
      "playwright-report/**",
      "test-results/**",
      "coverage/**",
      "node_modules/**",
      "**/*.config.*",
    ],
  },

  // General JS recommended.
  js.configs.recommended,

  // TypeScript typed lint for all .ts/.tsx, adding projectService in src/** below.
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

  // jsx-a11y recommended, nonstrict.
  jsxA11y.flatConfigs.recommended,

  // Source typed-lint language options.
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

  // Page suites/recording scripts obtain types through e2e/tsconfig.json.
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

  // Page scenarios never handwrite animation waits: Promise.all finished rejects cancelled transitions, while global waits hang on loops.
  // Shared entries await entrances before probes; midscenario overlays use support/region-helpers waitForEntrance.
  {
    files: ["e2e/regions/**/*.ts"],
    rules: {
      "no-restricted-syntax": ["error", {
        selector: "MemberExpression[property.name='getAnimations']",
        message: "Do not handwrite animation waits in page scenarios: use waitForEntrance from e2e/support/region-helpers.ts or the shared prescan wait in defineRegionScenarios.",
      }],
    },
  },

  // Tests disable typed lint.
  {
    files: ["**/*.test.{ts,tsx}"],
    ...tseslint.configs.disableTypeChecked,
  },
  // Tests also disable all jsx-a11y rules, allowing accessibility counterexamples as assertion targets.
  {
    files: ["**/*.test.{ts,tsx}"],
    rules: Object.fromEntries(
      Object.keys(jsxA11y.flatConfigs.recommended.rules).map((rule) => [rule, "off"]),
    ),
  },

  // Tests relax any/unsafe-* for mocks.
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

  // Project convention treats _-prefixed variables/parameters as intentionally unused.
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

  // Stricter than recommended: exhaustive-deps/incompatible-library always error.
  {
    rules: {
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/incompatible-library": "error",
    },
  },

  // Direct API restrictions, two disciplines:
  // - Enqueue APIs only through src/actions/, centralizing optimistic marks/dedupe notices/return
  //   normalization; direct component calls bypass side effects. New methods add api.ts
  //   @enqueue and ENQUEUE_METHODS entries, checked by api.enqueue-methods.test.ts.
  // - Capabilities only through src/hooks/useModelCapabilities, centralizing truths/invalidation/
  //   unknown-never-means-unsupported degradation, preventing catalog/server divergence.
  // - Never whole-module mock `@/api`, `@/api/*` or `react-i18next`; all files, no exemptions.
  // - Never discard refreshProject settlements (RESTRICT_DISCARDED_REFRESH); all files, no exemptions.
  // src/api.test.ts exempts first two, testing actual API paths/bodies.
  // Source additionally obeys @shadcn/lint/scroll/responsive/class guards, unlike class-asserting nonrendering tests.
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
  // Blocks relax their own constraints only, retaining others through restrictSyntax, as flat-config semantics above require.
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
  // Primitives own styles/overlay positions: disable restyle/arbitrary/static-class guards, permitting fixed inset-0, scrollHeight, viewport breakpoints,
  // native textarea/numeric layers/raw motion, while forbidding business dependencies.
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

  // Test trio: vitest (`expect-expect` catches zero assertions), testing-library, jest-dom.
  {
    files: TEST_FILES,
    plugins: { vitest },
    rules: {
      ...vitest.configs.recommended.rules,
      // Local `expectXxx(...)` helpers also count as assertions.
      "vitest/expect-expect": ["error", { assertFunctionNames: ["expect", "expect*"] }],
    },
  },
  {
    files: TEST_FILES,
    ...testingLibrary.configs["flat/react"],
    rules: {
      ...testingLibrary.configs["flat/react"].rules,
      // Legacy query/container style findings without autofix; review governs assertion strength, without prohibitions.
      "testing-library/no-node-access": "off",
      "testing-library/prefer-screen-queries": "off",
      "testing-library/no-container": "off",
      "testing-library/render-result-naming-convention": "off",
      "testing-library/no-unnecessary-act": "off",
      "testing-library/no-manual-cleanup": "off",
      // Three autofixes below produce syntax errors/semantic changes here: prefer-find-by breaks waitFor;
      // prefer-presence-queries replaces getBy feeding `.closest()` with nullable queryBy;
      // no-wait-for-multiple-assertions loses scope moving assertions out of callbacks.
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
      // `toHaveAttribute("aria-valuenow", …)` is misclassified as form-value assertions; autofix changes meaning.
      "jest-dom/prefer-to-have-value": "off",
    },
  },
);
