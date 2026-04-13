import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

// 迁移期 rule 降级清单 —— Task 3 通过 dry-run 填充。
// PR 2 / PR 3 每修完一类就从对应常量里删掉条目，rule 自动升回 recommended 的 error 级。
//
// 三个常量作用域说明：
//   MIGRATION_WARN_RULES_TYPED  —— 需要 type info 的 @typescript-eslint typed rules，
//                                   只对 src/**（非 test）文件生效，避免与
//                                   disableTypeChecked 冲突
//   MIGRATION_WARN_RULES_A11Y   —— jsx-a11y rules，不作用于 test 文件
//                                   （测试文件在下方 override 里整体 off，不应被 tail 覆回）
//   MIGRATION_WARN_RULES_ALL    —— 其他非 typed 非 a11y rule，全局生效
//
// PR 2 / PR 3 操作指引：
//   - 修完 @typescript-eslint typed 违规 → 从 MIGRATION_WARN_RULES_TYPED 删对应条目
//   - 修完 jsx-a11y 违规              → 从 MIGRATION_WARN_RULES_A11Y 删对应条目
//   - 修完其他违规                    → 从 MIGRATION_WARN_RULES_ALL 删对应条目
const MIGRATION_WARN_RULES_TYPED = {
  // --- Task 3 dry-run 填充（2026-04-13）---
  "@typescript-eslint/no-floating-promises": "warn",
  "@typescript-eslint/no-misused-promises": "warn",
  "@typescript-eslint/no-redundant-type-constituents": "warn",
  "@typescript-eslint/no-unnecessary-type-assertion": "warn",
  "@typescript-eslint/no-unsafe-argument": "warn",
  "@typescript-eslint/no-unsafe-assignment": "warn",
  "@typescript-eslint/no-unsafe-call": "warn",
  "@typescript-eslint/no-unsafe-member-access": "warn",
  "@typescript-eslint/no-unsafe-return": "warn",
  "@typescript-eslint/require-await": "warn",
};

// jsx-a11y 迁移清单 —— 不作用于 test 文件（测试文件在下方 override 里整体 off）
// PR 2 / PR 3 修完 a11y 违规后，从这里删除对应条目，rule 自动升回 recommended 的 error。
const MIGRATION_WARN_RULES_A11Y = {
  "jsx-a11y/click-events-have-key-events": "warn",
  "jsx-a11y/media-has-caption": "warn",
  "jsx-a11y/no-autofocus": "warn",
  "jsx-a11y/no-noninteractive-element-interactions": "warn",
  "jsx-a11y/no-static-element-interactions": "warn",
};

const MIGRATION_WARN_RULES_ALL = {
  // --- Task 3 dry-run 填充（2026-04-13）---
  "@typescript-eslint/no-explicit-any": "warn",
  "@typescript-eslint/no-unused-vars": "warn",
  "no-unsafe-finally": "warn",
  "react-hooks/refs": "warn",
  "react-hooks/set-state-in-effect": "warn",
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

  // 迁移期降级：typed rules 仅对 src/**（非 test）文件生效，避免与 disableTypeChecked 冲突
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["**/*.test.{ts,tsx}"],
    rules: MIGRATION_WARN_RULES_TYPED,
  },
  // a11y 迁移降级，只作用于非 test 文件
  {
    ignores: ["**/*.test.{ts,tsx}"],
    rules: MIGRATION_WARN_RULES_A11Y,
  },
  // 迁移期降级：不依赖 type info 的 rule，全局生效
  { rules: MIGRATION_WARN_RULES_ALL },
);
