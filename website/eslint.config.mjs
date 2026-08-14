// 与 frontend/eslint.config.js 规则集同构但刻意不共用：website 是独立包根，
// 其 typescript 钉在 5.x（见 .github/dependabot.yml 的说明），两边无法共享同一份
// 依赖与 tsconfig。改动规则时两份配置需要各自维护。
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default tseslint.config(
  {
    ignores: [".docusaurus/**", "build/**", "node_modules/**"],
  },

  js.configs.recommended,

  // TypeScript + typed linting（站点配置、sidebars 与 src/ 下的 swizzle 组件）
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
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // 构建脚本走 .mjs，不在上面那个 projectService 配置块的 files 范围内，拿不到类型
  // 信息，typed 规则只会整片报错，故关闭。disableTypeChecked 自带 languageOptions，
  // 必须与 globals 分成两个配置块，否则后者会被整体替换掉。
  {
    files: ["scripts/**/*.mjs", "*.config.mjs"],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    files: ["scripts/**/*.mjs", "*.config.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },

  // swizzle 组件消费 @theme/* 与 @docusaurus/theme-common/internal。typed linting 对这两个
  // 模块的推导不稳定：组件签名推导链路上的参数与返回值退化成 any，而 tsc 对同一段代码解析
  // 完整。于是 no-unsafe-* 在本目录只会产出假阳性——类型正确性由 typecheck 那道闸保证。
  {
    files: ["src/theme/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },

  // 项目惯例：_ 前缀变量/参数视为有意忽略，不报 unused-vars
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          varsIgnorePattern: "^_",
          argsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          destructuredArrayIgnorePattern: "^_",
        },
      ],
      "react-hooks/exhaustive-deps": "error",
    },
  },
);
