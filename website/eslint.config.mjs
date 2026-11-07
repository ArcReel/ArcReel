// Mirrors frontend/eslint.config.js rules but deliberately does not share them: website has an independent package root,
// with typescript pinned to 5.x (see .github/dependabot.yml), so the two cannot share
// dependencies or tsconfig. Maintain both configurations separately when changing rules.
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

  // TypeScript + typed linting (site configuration, sidebars, and swizzled components under src/)
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

  // Build scripts use .mjs outside the preceding projectService files scope and lack type
  // information, causing blanket typed-rule errors; disable them. disableTypeChecked supplies languageOptions,
  // so keep it separate from globals or the latter is replaced entirely.
  {
    files: ["scripts/**/*.mjs", "*.config.mjs"],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    files: ["scripts/**/*.mjs", "*.config.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },

  // Swizzled components consume @theme/* and @docusaurus/theme-common/internal. Typed linting infers these two
  // modules unstably: parameters and returns along component-signature inference degrade to any, while tsc resolves the same code
  // fully. Thus no-unsafe-* produces only false positives here; the typecheck gate verifies type correctness.
  {
    files: ["src/theme/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },

  // Repository convention: _-prefixed variables/parameters are intentionally ignored, without unused-vars errors
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
