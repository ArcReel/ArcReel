# 前端 ESLint + jsx-a11y 基础设施（PR 1）

- **日期**：2026-04-13
- **对应 Issue**：#219 (a11y audit)
- **PR 标题**（建议）：`chore(frontend): introduce ESLint + jsx-a11y`
- **作者**：Pollo

## 背景

Issue #219 在 Episode Cost Estimation 代码审查中发现全站交互元素存在多类 a11y 问题：focus-visible 可见态缺失、textarea 缺少 label 关联、progressbar 缺少 ARIA、隐藏 file input 缺少 aria-label 等。原 issue 建议人工 audit 全站组件。

项目前端目前**没有 ESLint**（只有 TypeScript + Vitest）。纯人工 audit 风险：(1) 容易漏掉新类违规；(2) 修复后无自动化防护，未来会重新积累类似问题。因此决定先建立前端 a11y 工程化基础设施，再做业务修复。

完整修复工作按「先建闸，再修复」分成 3 个 PR：

```
PR 1（本 spec）                  — ESLint + jsx-a11y 基础设施，零业务代码改动
  ↓
PR 2（#219 主题修复）             — focus-visible 统一 + textarea label + progressbar ARIA + file input aria-label
  ↓
PR 3（jsx-a11y 扫尾）             — 修剩余 warning、所有 rule flip to error、移除 --max-warnings
```

本 spec 仅覆盖 **PR 1**。PR 2、PR 3 各走独立 brainstorming/spec。

## 范围

### PR 1 做的事

1. 新增前端 ESLint 工具链到 `frontend/package.json` devDependencies（见「工具链版本」）
2. 新增 `frontend/eslint.config.js`（flat config，见「ESLint 配置」）
3. 更新 `frontend/package.json` scripts：新增 `lint` / `lint:fix`，`check` 加入 lint 步骤
4. 更新 `.github/workflows/test.yml` 的 `frontend-tests` job：在 typecheck 之后、测试之前新增 Lint step
5. 更新 `CONTRIBUTING.md`：新增前端 lint 相关段落，包含规则集说明、baseline ratchet 操作、本地 IDE 建议
6. `pnpm install` 更新 `frontend/pnpm-lock.yaml`

### PR 1 **不做**的事

- ❌ 修复任何 jsx-a11y 扫出的既有违规（→ PR 2 / PR 3）
- ❌ 修复任何 typed-linting 扫出的既有违规（含 `no-floating-promises` 等，即使肉眼判定是真 bug）
- ❌ 修复任何 `src/**` 业务代码（严格零业务改动）
- ❌ Prettier（项目当前未使用）
- ❌ `eslint-plugin-tailwindcss`（YAGNI，Tailwind v4 对其兼容性不稳）
- ❌ `eslint-plugin-import`（ESM + typescript-eslint 已满足基础 import 检查）
- ❌ 提交 `.vscode/` 配置到 repo（已在 `.gitignore:21`；CONTRIBUTING.md 建议用户本地添加）
- ❌ 替换 `confirm()` / 创建 `<ConfirmDialog>`（原 issue 正文提及，但独立工单）

### 依赖 / 时序

```
PR 1 merge 后开始 PR 2 开发 → PR 2 merge 后开始 PR 3 开发
```

PR 2 会降低 `--max-warnings` 数字，并从 `MIGRATION_WARN_RULES` 中删除对应的 rule 条目（rule 自动升回 error）。PR 3 降至 0 后移除 `--max-warnings` 参数、清空 `MIGRATION_WARN_RULES`。

## 技术决策

### 工具链版本（已验证 peer / engines 兼容）

```json
{
  "devDependencies": {
    "eslint": "^9.39.4",
    "@eslint/js": "^9.39.4",
    "typescript-eslint": "^8.58.1",
    "eslint-plugin-react": "^7.37.5",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-jsx-a11y": "^6.10.2",
    "globals": "^17.5.0"
  }
}
```

**锁 eslint v9 系列的理由**：

- `eslint@10.2.0` 已发布，`typescript-eslint@8.58.1` 已支持（peer `^10.0.0`）
- `eslint-plugin-react-hooks@7.0.1`（最新）的 peer 仍是 `eslint ^9.0.0`，不声明支持 v10
- 为避免 peer dependency 强制解析冲突，PR 1 锁 v9.39.4（v9 最新）
- 待 react-hooks 发布支持 v10 的版本后，以独立工单升级（与 PR 2/3 解耦）

**TypeScript 兼容性**：项目 TS `6.0.2`，typescript-eslint 8.58.1 的 peer 为 `typescript >=4.8.4 <6.1.0`，兼容。若未来 TS 升到 `6.1+`，需要同步升 typescript-eslint。

**Node engines**：项目 `>=20.19.0`，满足 eslint v9 的 Node 要求。

### ESLint 配置（`frontend/eslint.config.js`）

flat config 结构，采用 `tseslint.config()` helper。核心结构：

```js
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

// 迁移期 rule 降级清单：首次 lint dry-run 后，把所有触发 error 的 rule
// 列在这里降为 warn。PR 2/PR 3 每修完一类就从此处删除对应条目，
// 自动回到预设的 error 级别。
const MIGRATION_WARN_RULES = {
  // jsx-a11y（占多数，issue #219 主题）
  "jsx-a11y/label-has-associated-control": "warn",
  "jsx-a11y/click-events-have-key-events": "warn",
  "jsx-a11y/no-static-element-interactions": "warn",
  "jsx-a11y/alt-text": "warn",
  // typescript-eslint typed linting
  "@typescript-eslint/no-floating-promises": "warn",
  "@typescript-eslint/no-misused-promises": "warn",
  "@typescript-eslint/no-explicit-any": "warn",
  // ... 其余条目根据 dry-run 结果填充
};

export default tseslint.config(
  { ignores: ["dist/**", "coverage/**", "node_modules/**", "*.config.js"] },

  // 通用 JS
  js.configs.recommended,

  // TypeScript + typed linting
  ...tseslint.configs.recommendedTypeChecked,

  // React 19
  {
    ...react.configs.flat.recommended,
    settings: { react: { version: "19" } },
  },
  react.configs.flat["jsx-runtime"],

  // React Hooks
  {
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },

  // jsx-a11y recommended（不使用 strict）
  jsxA11y.flatConfigs.recommended,

  // 源码通用语言选项
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
  // 测试文件：额外关闭 jsx-a11y（vitest/testing-library 用 a11y 反例做断言目标）
  {
    files: ["**/*.test.{ts,tsx}"],
    rules: Object.fromEntries(
      Object.keys(jsxA11y.flatConfigs.recommended.rules).map((rule) => [rule, "off"]),
    ),
  },

  // 迁移期降级（放最后，覆盖前面的 recommended 预设）
  { rules: MIGRATION_WARN_RULES },
);
```

### 关键配置决策

- **`MIGRATION_WARN_RULES` 作为迁移窗口**：保留 recommended 预设的 error/warn 层次，但对"项目现状违反"的 rule 单独降级为 warn。PR 2/3 的修复节奏和此列表一一对应——修完一类删掉对应条目，rule 自动升回 error。比"全局降 warn"更保留 ESLint 预设意图。
- **`*.config.js` 放进 `ignores`**：`vite.config.ts`、`vitest.config.ts`、`eslint.config.js` 自身不参与 typed linting，避免 tsconfig 覆盖扯皮。
- **测试文件分两层 override**：先 `disableTypeChecked` 关 typed linting，再用 `Object.fromEntries` 动态生成的 `{ rule: "off" }` 关所有 jsx-a11y rule。分两个 config entry 是为了来源清晰，后续重开 jsx-a11y 只需删第二块。
- **`parserOptions.projectService: true`**：typescript-eslint 8 推荐的新 API，自动发现 tsconfig，性能显著优于旧 `parserOptions.project`。
- **`jsx-a11y/recommended` 而非 `strict`**：strict 会额外扫出 30-60 处违规（图标按钮无 aria-label 等），对本轮 audit 是过大负担；PR 3 扫尾完成后可独立评估是否升级到 strict。
- **`typescript-eslint/recommendedTypeChecked`** 启用：项目 async 密集（API 调用、SSE、Agent SDK），`no-floating-promises` / `no-misused-promises` 的 ROI 高。

### CI 集成（`.github/workflows/test.yml`）

在 `frontend-tests` job 中，`Type check` step 之后、`Run tests` 之前插入：

```yaml
- name: Lint
  working-directory: frontend
  run: pnpm lint
```

**保留三个独立 step** 而非用 `pnpm check` 合并：GitHub Actions UI 里 typecheck / lint / test 分开着色，诊断更快。

### `frontend/package.json` scripts

```diff
  "scripts": {
    "dev": "vite",
    "typecheck": "tsc --noEmit",
+   "lint": "eslint . --max-warnings=<BASELINE>",
+   "lint:fix": "eslint . --fix",
    "build": "pnpm typecheck && vite build",
-   "check": "pnpm typecheck && vitest run",
+   "check": "pnpm typecheck && pnpm lint && vitest run",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  }
```

## baseline ratchet 操作

### PR 1 如何确定 `<BASELINE>` 数字

1. 装完 deps 后本地跑：`cd frontend && pnpm exec eslint . --max-warnings=9999`
2. 获得实际 warning 数量 N
3. 将 `package.json` 里 `"lint"` 命令的 `--max-warnings=<BASELINE>` 替换为 `--max-warnings=N`
4. 提交

N 的预估：30–80，但以实际为准。

### PR 2/PR 3 如何降低 baseline

1. 本地 `cd frontend && pnpm lint` 看当前 warning 数
2. 修复 warning 直到数字变小
3. 更新 `package.json` 里 `--max-warnings=<N>` 为当前数字
4. 同步删掉 `eslint.config.js` 里 `MIGRATION_WARN_RULES` 中已清零的 rule 条目
5. 提交，CI 验证

### 「误改大」场景分析

如果 PR 作者把 `--max-warnings` 改大，CI 的 lint step 会通过（只要实际 warning 数 ≤ 新 N）。这有两种子情况：

- **没改代码、只改大 N**：warning 数保持原值，CI pass；后果只是"baseline 虚高"，未引入新违规，不算危险。
- **改代码引入新违规 + 同步改大 N**：warning 数上升到新 N 以下，CI pass——这是**真"假绿"**，CI 无法自动拦截。

因此：`--max-warnings` 的数字在 PR diff 里**只允许减不允许加**，由 CR 人工把关。不引入自动化脚本（历史 baseline 追踪脚本带来的维护负担 > 手动 review 成本）。CR checklist 里补一条："如果 `package.json` 的 `--max-warnings` 数字变化，需同步检查是否有新引入的违规"。

## `CONTRIBUTING.md` 文档更新

在现有「代码质量」章节附近新增一个前端 lint 小节，内容：

1. **本地运行**：`pnpm lint` / `pnpm lint:fix`
2. **规则集说明**：基于 `jsx-a11y/recommended` + `typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended`
3. **baseline ratchet 机制**：`--max-warnings=<N>` 数字 = 未修的历史违规数；PR 2/3 会逐步降到 0 并移除该参数
4. **PR 2/3 作者的操作清单**（即「baseline ratchet 操作」章节的 5 步）
5. **本地 IDE 建议**（不提交 repo）：
   ```json
   // .vscode/settings.json (本地添加，`.vscode/` 已 gitignored)
   {
     "eslint.workingDirectories": [{ "pattern": "./frontend" }],
     "editor.codeActionsOnSave": { "source.fixAll.eslint": "explicit" }
   }
   ```
6. **已知约束**：eslint 锁在 v9 系列，等 `eslint-plugin-react-hooks` 支持 v10 后独立升级

## 验收标准

PR 1 完工 = 以下全部满足：

1. ✅ `pnpm install` 成功，无 peer dependency 冲突警告
2. ✅ `pnpm lint` 返回 0：实际 warning 数量 ≤ `package.json` 中 `--max-warnings=N` 设定的 N，error 数量为 0
3. ✅ `pnpm check` 返回 0：typecheck + lint + vitest 全通过
4. ✅ GitHub Actions `frontend-tests` job 全绿，新 `Lint` step 可见且通过
5. ✅ 最终 diff 文件清单与「PR 1 做的事」一致，**零 `src/**` 业务代码改动**

## 风险与缓解

| # | 风险 | 缓解 |
|---|------|------|
| 1 | typed linting 扫出的 error 数量超预期，`MIGRATION_WARN_RULES` 变得很长 | 不是问题，列表结构清晰；PR 2/3 按类别逐批清零即可 |
| 2 | `projectService: true` 在本地 / CI 行为偶发不一致（monorepo 或特殊 tsconfig bug） | 若首次 CI 挂，退回旧 `parserOptions.project: './tsconfig.json'` 显式配置 |
| 3 | `eslint-plugin-react-hooks` 未支持 eslint v10 阻碍未来升级 | 锁 v9 是本 PR 的临时决策；升级为独立工单 |
| 4 | 开发者 IDE 未装 ESLint 扩展，本地不显示红线 | `pnpm check` 兜底；CONTRIBUTING.md 建议装扩展 |
| 5 | `<BASELINE>` 占位符未替换就提交 | CI 早失败（`--max-warnings=<BASELINE>` 解析报错），天然拦截 |
| 6 | Lint step 在 CI 上比 typecheck 更慢（typed linting 冷启动 15-25s） | 可接受，无需并行化 |

## 非目标

- 修复任何既有违规（PR 2 / PR 3 负责）
- 引入 Prettier（独立工单）
- 引入 `eslint-plugin-tailwindcss` / `eslint-plugin-import`（YAGNI）
- 创建 `<ConfirmDialog>` 组件、替换 `window.confirm()`（独立 issue）
- 修改 Node 版本策略（当前版本已满足 eslint v9）
- 升级 ESLint 到 v10（等 react-hooks 支持后独立工单）

## Brainstorming 过程记录

为便于未来追溯决策来源，列出本次 brainstorming 中用户确认的关键选择：

| 决策维度 | 选项 | 备注 |
|----------|------|------|
| Issue #219 整体范围 | B → focus-visible + textarea label + progressbar ARIA + file input aria-label | 留给 PR 2 |
| focus-ring token 载体 | B → `@utility` 指令（Tailwind v4 推荐） | 留给 PR 2 |
| focus-ring 视觉规格 | B → `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500` | 留给 PR 2 |
| 碎片化防护 | C → 引入 ESLint + jsx-a11y | 本 PR 落地 |
| PR 拆分 | B → 3 个独立 PR | 本 PR 为其中 PR 1 |
| 既有违规处理 | B → `--max-warnings=<baseline>` ratchet | 本 PR 落地 |
| typed linting | B → 启用 + `projectService: true` | 本 PR 落地 |
| 组件抽象策略 | B → 只抽 `<ProgressBar>` | 留给 PR 2 |
| PR 1 是否修 "明显 bug" | 不修（严格零业务代码改动） | 本 PR |
| `.vscode/` 处理 | 保持 ignored，CONTRIBUTING.md 建议本地添加 | 本 PR |
| jsx-a11y 规则集 | `recommended`（不用 strict） | 本 PR |
| 前端 lint 文档位置 | `CONTRIBUTING.md`（不新增 frontend/README.md） | 本 PR |
