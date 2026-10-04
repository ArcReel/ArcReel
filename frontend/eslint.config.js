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

// ---------------------------------------------------------------------------
// 已重做区域：@shadcn/lint 与滚动、响应式守卫只对这里列出的 glob 生效。
// 每个区域重做的 ticket 把自己的目录加进来；交付结束时整体替换为 "src/**"。
const REWORKED_FILES = [
  // 自动撑高输入框
  "src/components/ui/textarea.tsx",
  // 弹层、反馈与动效原语，及首批迁移的共享弹层与全局提示
  "src/components/ui/**",
  "src/components/shared/ArchiveDiagnosticsDialog.tsx",
  "src/components/shared/PromptPreviewButton.tsx",
  "src/components/shared/ScriptOverwriteConfirmDialog.tsx",
  "src/components/shared/TruncatedText.tsx",
  "src/components/layout/ToastOverlay.tsx",
  // 编辑单元、保存栏、内联未保存提示条与离开拦截
  "src/components/shared/edit-unit/**",
  // 页面外壳与全局设置导航
  "src/components/shared/page-shell/**",
  "src/components/pages/SystemConfigPage.tsx",
  "src/components/pages/settings/GeneralSection.tsx",
  // 外部 Agent 接入与访问令牌
  "src/components/pages/settings/agent-access/**",
  // 全局设置「ArcReel Agent」：Agent 供应商列表、添加与编辑对话框、运行参数
  "src/components/pages/AgentConfigTab.tsx",
  "src/components/agent/{AddCredentialModal,CredentialList,CredentialsSection,ModelIdField,TestResultPanel}.tsx",
  // 供应商分区：二级栏与详情栏（主从布局共用件）、预置供应商详情与密钥
  "src/components/shared/master-detail/**",
  "src/components/pages/ProviderSection.tsx",
  "src/components/pages/ProviderDetail.tsx",
  "src/components/pages/CredentialList.tsx",
  // 使用记录（设置页分区与顶栏弹层）
  "src/components/usage/**",
  // 默认模型与模型选择字段族、提示词模版、关于与配置问题就地提示
  "src/components/shared/{ProviderModelSelect,LayeredModelFields,ModelConfigSection,TextTierFields,NarrationDeliveryFields,ResolutionPicker,VideoModelSpecBar,InlineWarning}.tsx",
  "src/components/pages/settings/{MediaModelSection,AboutSection,ConfigIssueNotice}.tsx",
  "src/components/pages/settings/{Prompt*,promptTemplate*}.tsx",
  // 调用端点分区：端点列表、详情、定义表单、端点测试与导入、分享对话框
  "src/components/pages/settings/endpoints/**",
  // 自定义供应商详情：表单、模型表格、调用端点选择与尾帧能力覆盖
  "src/components/pages/settings/{CustomProviderDetail,CustomProviderForm,CustomProviderFieldRow,CustomProviderModelTable,EndpointSelect,CapabilityOverrideRow}.tsx",
  // 市场分区（浏览、我的分享、设置）与全仓共用的可排序列表
  "src/components/pages/settings/market/**",
  "src/components/shared/sortable/**",
  // 登录页、404 页与导出范围对话框
  "src/components/pages/{LoginPage,NotFoundPage}.tsx",
  "src/components/layout/ExportScopeDialog.tsx",
  // 新建项目向导、共用的时长档位选择器、风格选择器与生成方式等字段
  "src/components/pages/CreateProjectModal.tsx",
  "src/components/pages/create-project/**",
  "src/components/shared/{DurationTierPicker,StylePicker,GenerationRouteCards,GridStoryboardBar,OptionalNumberField,EpisodeTargetDurationField,SpeechRateField}.tsx",
  // 项目大厅：顶栏、问候区、筛选工具栏、海报卡与项目对话框
  "src/components/pages/ProjectsPage.tsx",
  "src/components/pages/lobby/**",
  // 资产库：页面、卡片与详情 Sheet、应用到项目，以及画廊里的入库预览与「从资产库导入」
  "src/components/pages/AssetLibraryPage.tsx",
  "src/components/assets/{AddToLibraryButton,ApplyToProjectDialog,AssetCard,AssetCreateDialog,AssetDetailSheet,AssetPickerModal,AssetThumb,DeleteAssetDialog,LoadMoreSentinel,asset-type-icons,useAssetPages}.{ts,tsx}",
  // 项目设置：侧栏分页、风格对话框与 Agent 配置
  "src/components/pages/ProjectSettingsPage.tsx",
  "src/components/pages/project-settings/**",
  // 记忆编辑器：Agent 记忆分区与项目记忆分页共用的文件列表、编辑器与确认
  "src/components/agent-memory/**",
  // 项目工作区外壳：侧栏、画布区与 Agent 面板的分栏、顶栏 Agent 开关
  "src/components/layout/{StudioLayout,AssetSidebar,AgentPanelToggle,WorkspaceResizeHandle,workspace-layout}.{ts,tsx}",
  // 项目概览与空项目欢迎页：页头、资产完成度与费用、故事设定
  "src/components/canvas/overview/**",
  // 工作区顶栏（项目切换器、通知、导出）、侧栏集列表与项目内未知路径的空状态
  "src/components/layout/{GlobalHeader,ProjectMenu,WorkspaceNotificationsDrawer,SidebarEpisodeList,EpisodeCard,useProjectExport}.tsx",
  "src/components/canvas/WorkspaceNotFound.tsx",
  // 分集视图：集目录与原文、页头工具行、方案栏，以及上传原文、新建一集等对话框
  "src/components/canvas/episodes/**",
  // Agent 面板消息流：滚动跟随、消息行与原地编辑器、思考块、Markdown 正文、复制按钮与演示面板
  "src/components/copilot/chat/{MessageFlow,MessageRow,MessageEditor,ChatImage,ContentBlockRenderer,ThinkingBlock,TextBlock,display-items}.{ts,tsx}",
  "src/components/copilot/StreamMarkdown.tsx",
  "src/components/shared/CopyButton.tsx",
  "src/onboarding/DemoAssistantPanel.tsx",
  // Agent 面板顶栏与输入区：会话历史、提问、待办进度、输入框附件与斜杠命令菜单
  "src/components/copilot/{AgentCopilot,AgentComposer,AgentQuestionnaire,SessionHistory,SlashCommandMenu,TodoProgress}.tsx",
  // Agent 消息区的会话投影：失败卡片与「上下文已压缩」分隔线
  "src/components/copilot/chat/{AgentFailureCard,CompactionMarker}.tsx",
  // Agent 工序行：工具调用、子智能体、Skill、后台任务，及显示名与摘要格式
  "src/components/copilot/chat/{WorkRow,ToolCallWithResult,SubagentCard,SkillChip,TaskProgressBlock,work-label,arcreel-tool-summaries}.{ts,tsx}",
  // 剧集页头与视图路由：集头、视图 tab 与动作插槽、制作进度入口、批量补齐按钮与确认框、编写提示词入口
  "src/components/canvas/episode-page/**",
  "src/components/canvas/timeline/StoryboardBatchDialog.tsx",
  "src/components/canvas/shared/PromptAuthoringButton.tsx",
  // 多宫格分镜图视图：分组卡片、联合图面板与页头「补齐联合图」
  "src/components/canvas/grid/**",
  // 参考视频工作台：单元列表、文稿编辑器、成片预览与脚本规划预览
  "src/components/canvas/reference/**",
  // 剪辑视图（播放器、详情栏与轨道）与出片对话框、空状态
  "src/components/canvas/edit/**",
  "src/components/canvas/edit-render/**",
  // 分镜视图：画布主体、分镜列表、分镜详情的栏位与页头、媒体栏卡片、引用摘要、备注与版本历史
  "src/components/canvas/timeline/{TimelineCanvas,ShotSplitView,ShotList,ShotDetailLayout,ShotDetailHeader,TooltipIconButton,ShotStructureActions,EmptyScriptState,StatusBadge,SegmentBreakToggle,NotesDrawer,MediaCard,EndFrameRow,NarrationAudioCard,ReferencesSection,AvatarStack,ClueStack,RefThumbnail,VersionTimeMachine}.tsx",
  // 制作进度弹层内容：步骤行、提示、整批准入结论、进行中的任务与过期产物
  "src/components/workflow/**",
];
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

// @shadcn/lint 规则：components/ui 内的原语自身负责样式，关闭 restyle、任意值与静态 class 三条。
const SHADCN_RULES = {
  "shadcn/no-restyle": ["error", { allow: ["layout"] }],
  "shadcn/no-raw-colors": "error",
  "shadcn/no-arbitrary-values": ["error", { allow: ["layout"] }],
  "shadcn/no-inline-styles": "error",
  "shadcn/require-static-classes": "error",
  "shadcn/no-unknown-classes": "error",
};

// 已重做区域的配置块。no-restricted-syntax 按替换语义逐块列全（见下方 API 直调约束的说明）：
// 原语放过 fixed inset-0、scrollHeight 与视口断点，白名单文件放过视口断点。
// 入队动作层与 useModelCapabilities 各有专属豁免块，不纳入区域守卫，避免互相覆盖。
// flat config 的 files 不接受空数组，列表为空时不生成任何配置块。
const reworkedAreaConfigs = () => {
  if (REWORKED_FILES.length === 0) return [];
  const apiRules = [RESTRICT_ENQUEUE, RESTRICT_CAPABILITIES, RESTRICT_MODULE_MOCK];
  const ignores = [...TEST_FILES, "src/actions/**", "src/hooks/useModelCapabilities.ts"];
  // files 中的嵌套数组表示「同时匹配」：取已重做区域与给定范围的交集。
  const intersect = (scopes) => REWORKED_FILES.flatMap((glob) => scopes.map((scope) => [glob, scope]));
  const configs = [
    {
      files: REWORKED_FILES,
      ignores,
      plugins: { shadcn },
      rules: {
        ...SHADCN_RULES,
        "no-restricted-syntax": [
          "error",
          ...apiRules,
          ...RESTRICT_VIEWPORT_HEIGHT,
          ...RESTRICT_FIXED_OVERLAY,
          RESTRICT_SCROLL_HEIGHT,
          ...RESTRICT_VIEWPORT_BREAKPOINT,
        ],
      },
    },
    {
      files: intersect([UI_PRIMITIVES]),
      ignores,
      rules: {
        "shadcn/no-restyle": "off",
        "shadcn/no-arbitrary-values": "off",
        "shadcn/require-static-classes": "off",
        "no-restricted-syntax": ["error", ...apiRules, ...RESTRICT_VIEWPORT_HEIGHT],
      },
    },
  ];
  if (VIEWPORT_BREAKPOINT_ALLOWLIST.length > 0) {
    configs.push({
      files: intersect(VIEWPORT_BREAKPOINT_ALLOWLIST),
      ignores: [...ignores, UI_PRIMITIVES],
      rules: {
        "no-restricted-syntax": [
          "error",
          ...apiRules,
          ...RESTRICT_VIEWPORT_HEIGHT,
          ...RESTRICT_FIXED_OVERLAY,
          RESTRICT_SCROLL_HEIGHT,
        ],
      },
    });
  }
  return configs;
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

  // 页面级套件与录制脚本：类型信息取自 e2e/tsconfig.json；scripts/ 同理取自 scripts/tsconfig.json
  {
    files: ["e2e/**/*.ts", "scripts/**/*.ts"],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
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
  // src/api.test.ts 豁免前两条：它测试的是 API 层本体的端点路径与请求体。
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api.test.ts"],
    rules: {
      "no-restricted-syntax": ["error", RESTRICT_ENQUEUE, RESTRICT_CAPABILITIES, RESTRICT_MODULE_MOCK],
    },
  },
  {
    files: ["src/api.test.ts"],
    rules: {
      "no-restricted-syntax": ["error", RESTRICT_MODULE_MOCK],
    },
  },
  // 各自的实现方只豁免自己那条，另两条仍受约束（见文件头对 flat config 替换语义的说明）。
  {
    files: ["src/actions/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": ["error", RESTRICT_CAPABILITIES, RESTRICT_MODULE_MOCK],
    },
  },
  {
    files: ["src/hooks/useModelCapabilities.ts"],
    rules: {
      "no-restricted-syntax": ["error", RESTRICT_ENQUEUE, RESTRICT_MODULE_MOCK],
    },
  },

  // 已重做区域：@shadcn/lint 与滚动、响应式守卫（列表与规则定义见文件上方）。
  ...reworkedAreaConfigs(),

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
