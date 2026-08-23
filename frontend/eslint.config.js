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

const TEST_FILES = ["src/**/*.test.{ts,tsx}"];

// no-restricted-syntax 的各条约束定义在此处、由下方配置块组合。
// flat config 对同一文件匹配到的同名规则是「后者整体替换前者的选项」而非合并：若拆成多个
// 配置块各写一条 selector，文件范围重叠时先声明的那条会被静默摘除。故每个配置块都必须把
// 该文件应受的全部约束一次性列全，豁免用「少列一条」表达，而不是另起一块。
const RESTRICT_ENQUEUE = {
  selector:
    "CallExpression[callee.object.name='API'][callee.property.name=/^(generateStoryboard|generateVideo|generateNarrationAudio|generateEpisodeNarrationAudio|generateCharacter|generateCharacterVoiceSample|generateProjectScene|generateProjectProp|generateProjectProduct|editImage|generateGrid|regenerateGrid|generateReferenceVideoUnit|generateReferenceVideoBatch)$/]",
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
    "CallExpression[callee.object.name='vi'][callee.property.name='mock'][arguments.0.value=/^(@\\/api|react-i18next)$/]",
  message:
    "禁止整模块 mock：API 打桩用 vi.spyOn(API, method)；i18n 用全局 setup 已加载的真实中文资源（整体 mock 后翻译缺失无法被发现）。",
};

export default tseslint.config(
  // 全局 ignores —— 覆盖 *.config.js 和 *.config.ts（vite.config.ts、vitest.config.ts）
  {
    ignores: [
      "dist/**",
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
  // - 测试不得整模块 mock `@/api` 与 `react-i18next`，该条对全部文件生效、无豁免。
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
