# Frontend ESLint + jsx-a11y Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在前端接入 ESLint + `eslint-plugin-jsx-a11y`，建立 a11y 基础设施，所有既有违规以 warning 形式记录到 baseline，本 PR 零 `src/**` 业务代码改动。

**Architecture:** flat config（`eslint.config.js`），`tseslint.config()` helper 组合插件；`recommendedTypeChecked` 启用 typed linting；`jsx-a11y/recommended`；既有 error 级违规通过 `MIGRATION_WARN_RULES` 全局降 warn + `--max-warnings=<N>` ratchet 机制锁 baseline。CI 在 `.github/workflows/test.yml` 的 `frontend-tests` job 新增 `Lint` step。

**Tech Stack:** ESLint v9.39.4, typescript-eslint v8.58.1, eslint-plugin-react v7.37.5, eslint-plugin-react-hooks v7.0.1, eslint-plugin-jsx-a11y v6.10.2, globals v17.5.0, @eslint/js v9.39.4. flat config (ESM). pnpm v10. 前端 Node 20.19+。

**Spec:** `docs/superpowers/specs/2026-04-13-frontend-eslint-jsx-a11y-design.md`

---

## File Structure

**Created:**
- `frontend/eslint.config.js` — flat config 入口，组合所有插件、声明 ignores、`MIGRATION_WARN_RULES` 迁移窗口、测试文件 override。文件位置在 `frontend/` 根目录，和 `package.json`、`tsconfig.json` 同级。

**Modified:**
- `frontend/package.json` — 新增 7 个 devDependencies（eslint + 插件）、新增 `lint` / `lint:fix` scripts、`check` 串入 `pnpm lint`
- `frontend/pnpm-lock.yaml` — `pnpm install` 自动更新
- `.github/workflows/test.yml` — `frontend-tests` job 在 `Type check` 后、`Run tests` 前插入 `Lint` step
- `CONTRIBUTING.md` — 「代码质量」章节 ruff 段落之后新增「前端 ESLint」段落

**Zero changes:** `frontend/src/**/*` — 严格零业务代码改动。

**Branch:** `fix/a11y-focus-visible-219` (当前 worktree)。PR 1 使用此分支，PR title 建议 `chore(frontend): introduce ESLint + jsx-a11y (#219)`。

---

## Task 1: 添加 ESLint 工具链依赖并安装

**Files:**
- Modify: `frontend/package.json:37-51` (devDependencies block)
- Modify: `frontend/pnpm-lock.yaml` (auto-regenerated)

- [ ] **Step 1: 编辑 `frontend/package.json` devDependencies 块，新增 ESLint 工具链 7 个包（按字母序插入）**

把 `frontend/package.json` 的 `devDependencies` 从：

```json
  "devDependencies": {
    "@tailwindcss/vite": "^4.2.2",
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@testing-library/user-event": "^14.6.1",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "@vitest/coverage-v8": "^4.1.2",
    "jsdom": "^29.0.1",
    "tailwindcss": "^4.2.2",
    "typescript": "^6.0.2",
    "vite": "^8.0.3",
    "vitest": "^4.1.2"
  },
```

改为：

```json
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@tailwindcss/vite": "^4.2.2",
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@testing-library/user-event": "^14.6.1",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "@vitest/coverage-v8": "^4.1.2",
    "eslint": "^9.39.4",
    "eslint-plugin-jsx-a11y": "^6.10.2",
    "eslint-plugin-react": "^7.37.5",
    "eslint-plugin-react-hooks": "^7.0.1",
    "globals": "^17.5.0",
    "jsdom": "^29.0.1",
    "tailwindcss": "^4.2.2",
    "typescript": "^6.0.2",
    "typescript-eslint": "^8.58.1",
    "vite": "^8.0.3",
    "vitest": "^4.1.2"
  },
```

- [ ] **Step 2: 运行 `pnpm install` 更新 lockfile**

Run: `cd frontend && pnpm install`

Expected: 输出含 `Progress: resolved ... added ...`；结束无 ERROR；有 WARN 但不含 peer dependency 冲突错误。如果出现 peer 警告，检查其是否涉及 eslint/typescript-eslint/react 链条——若是 react-hooks 声明 `eslint ^9.0.0`、我们装的是 `^9.39.4`，peer 满足，应无警告。

- [ ] **Step 3: 验证新 binary 可被 pnpm 访问**

Run: `cd frontend && pnpm exec eslint --version`

Expected: 输出 `v9.39.4`（或更高 patch）。

- [ ] **Step 4: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-visible-219
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "chore(frontend): add ESLint + jsx-a11y dev dependencies

锁 eslint v9（eslint-plugin-react-hooks v7 peer 还未支持 v10）。
本 commit 仅装依赖，不新增配置文件，pnpm lint 暂未可用。"
```

---

## Task 2: 创建基础 `eslint.config.js`（flat config 骨架）

**Files:**
- Create: `frontend/eslint.config.js`

**说明：** 本 task 只建立 config 骨架，`MIGRATION_WARN_RULES` 初始为空对象；Task 3 再通过 dry-run 迭代填充。

- [ ] **Step 1: 创建 `frontend/eslint.config.js`，写入以下内容**

```js
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

// 迁移期 rule 降级清单 —— Task 3 通过 dry-run 填充。
// PR 2 / PR 3 每修完一类就删掉对应条目，rule 自动升回 recommended 的 error 级。
const MIGRATION_WARN_RULES = {};

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

  // 迁移期降级（放最后，覆盖前面的 recommended 预设）
  { rules: MIGRATION_WARN_RULES },
);
```

- [ ] **Step 2: 验证 config 语法正确、eslint 可加载**

Run: `cd frontend && pnpm exec eslint --print-config src/App.tsx > /dev/null`

Expected: 命令正常退出（exit code 0），无报错；如有任何 `ConfigError` / `ReferenceError`，修正语法再重试。此命令只加载 config 并打印对 `src/App.tsx` 生效的规则，不会产出 lint 违规。

- [ ] **Step 3: Commit**

```bash
git add frontend/eslint.config.js
git commit -m "chore(frontend): add eslint.config.js (flat config) skeleton

未填充 MIGRATION_WARN_RULES，pnpm lint 会有大量 error。
Task 3 通过 dry-run 迭代填充迁移窗口。"
```

---

## Task 3: dry-run 迭代，把既有 error 全部降级为 warning

**Files:**
- Modify: `frontend/eslint.config.js` (`MIGRATION_WARN_RULES` constant)

**说明：** 本 task 的目标是让 `pnpm exec eslint .` 返回 0 error、N warning。通过 JSON 报告抓出所有 error 级别的 rule，全部加入 `MIGRATION_WARN_RULES` 降为 warn。N 将在 Task 4 写入 `package.json`。

- [ ] **Step 1: 跑首次 dry-run，生成 JSON 报告**

Run: `cd frontend && pnpm exec eslint . --max-warnings=9999 --format=json --no-error-on-unmatched-pattern > /tmp/eslint-report.json 2>/dev/null; echo "exit=$?"`

Expected: 输出 `exit=1`（因为有 error，正常现象）；`/tmp/eslint-report.json` 存在且是有效 JSON。

**如果** 命令本身崩溃（非 exit 1，而是 exit 2 + stderr 输出 `ConfigError` / `Error:`），大概率是 typed linting 扫到 `src/**` 外的 `.ts` 文件（如 `scripts/`、根目录的其他 `.ts`），或者某个 `src/**/*.ts` 不在 `tsconfig.json` include 里。处理方式：

- 如果错误信息类似 `parserOptions.project has been set, but the file ... is not in any tsconfig.json`，检查 `frontend/tsconfig.json:include` 是否覆盖该文件；或在 `eslint.config.js` 的 ignores 里加入该路径；或把 typed linting 限定更严的 `files` glob。
- 如果错误信息类似 `Cannot find module`，检查 `import.meta.dirname` 是否可用（Node 20.11+），或改用 `path.dirname(fileURLToPath(import.meta.url))`。

- [ ] **Step 2: 从 JSON 报告中抽取所有 error 级别的 rule 名**

Run:

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-visible-219/frontend
node -e '
  const data = JSON.parse(require("fs").readFileSync("/tmp/eslint-report.json", "utf8"));
  const errorRules = new Set();
  const warnRules = new Set();
  for (const f of data) {
    for (const m of f.messages) {
      if (!m.ruleId) continue;
      if (m.severity === 2) errorRules.add(m.ruleId);
      else if (m.severity === 1) warnRules.add(m.ruleId);
    }
  }
  console.log("=== ERROR rules (need MIGRATION_WARN_RULES) ===");
  [...errorRules].sort().forEach((r) => console.log(`  "${r}": "warn",`));
  console.log("\n=== Current WARN rules (no action) ===");
  [...warnRules].sort().forEach((r) => console.log(`  ${r}`));
  const totalErr = data.reduce((a, f) => a + f.errorCount, 0);
  const totalWarn = data.reduce((a, f) => a + f.warningCount, 0);
  console.log(`\n=== Totals: ${totalErr} errors, ${totalWarn} warnings ===`);
'
```

Expected: 输出一个 `"rule-name": "warn",` 列表（粘贴用），以及当前 error / warning 总数。抄下"ERROR rules"列表准备编辑 config。

- [ ] **Step 3: 把抽出的 error rule 列表粘贴进 `MIGRATION_WARN_RULES`**

编辑 `frontend/eslint.config.js`，把 `const MIGRATION_WARN_RULES = {};` 替换为：

```js
const MIGRATION_WARN_RULES = {
  // --- Task 3 dry-run 填充（2026-04-13）---
  // <把 Step 2 输出的所有 "rule-name": "warn", 行粘贴到这里，按首字母分组>
  // 例（实际内容以 dry-run 为准）：
  "@typescript-eslint/no-floating-promises": "warn",
  "@typescript-eslint/no-misused-promises": "warn",
  "@typescript-eslint/no-unsafe-argument": "warn",
  "jsx-a11y/alt-text": "warn",
  "jsx-a11y/click-events-have-key-events": "warn",
  "jsx-a11y/label-has-associated-control": "warn",
  "jsx-a11y/no-static-element-interactions": "warn",
  // ... 所有 dry-run 输出的条目
};
```

- [ ] **Step 4: 再跑一次 lint，确认已经零 error**

Run: `cd frontend && pnpm exec eslint . --max-warnings=9999 --no-error-on-unmatched-pattern; echo "exit=$?"`

Expected: `exit=0`；输出结尾有形如 `✖ N problems (0 errors, N warnings)` 的一行。记下 N（warning 总数），Task 4 会用。

**如果仍有 error**：把新报告的 error rule 追加到 `MIGRATION_WARN_RULES`，重复 Step 4 直到 error 为 0。

- [ ] **Step 5: 记录 baseline N 到临时 note（Task 4 会用）**

Run:

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-visible-219/frontend
pnpm exec eslint . --max-warnings=9999 --no-error-on-unmatched-pattern 2>&1 \
  | tail -5
```

记下 `N warnings` 中的 N。

- [ ] **Step 6: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-visible-219
git add frontend/eslint.config.js
git commit -m "chore(frontend): populate MIGRATION_WARN_RULES migration window

所有 recommended 预设下现有违规的 rule 降为 warn。
pnpm exec eslint . 现在 0 error, <N> warnings。
Task 4 会把 N 写入 package.json 的 --max-warnings。"
```

---

## Task 4: 加 `lint` / `lint:fix` scripts 并把 BASELINE 写入

**Files:**
- Modify: `frontend/package.json:9-18` (scripts block)

**说明：** 把 Task 3 记下的 N 写入 `--max-warnings`。本 task 结束后 `pnpm lint` 可用。

- [ ] **Step 1: 修改 `frontend/package.json` 的 scripts 块**

把 `frontend/package.json` 的 scripts 从：

```json
  "scripts": {
    "dev": "vite",
    "typecheck": "tsc --noEmit",
    "build": "pnpm typecheck && vite build",
    "check": "pnpm typecheck && vitest run",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  },
```

改为（把 `<N>` 替换为 Task 3 记下的实际数字）：

```json
  "scripts": {
    "dev": "vite",
    "typecheck": "tsc --noEmit",
    "lint": "eslint . --max-warnings=<N>",
    "lint:fix": "eslint . --fix",
    "build": "pnpm typecheck && vite build",
    "check": "pnpm typecheck && pnpm lint && vitest run",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  },
```

**例**：若 Task 3 Step 5 显示 47 warnings，则 `"lint": "eslint . --max-warnings=47"`。

- [ ] **Step 2: 验证 `pnpm lint` 正常返回 0**

Run: `cd frontend && pnpm lint; echo "exit=$?"`

Expected: `exit=0`；输出末尾 `✖ <N> problems (0 errors, <N> warnings)`，其中 N 等于 `--max-warnings` 设定值。

**如果** exit != 0：最可能是 `--max-warnings` 数字比实际 warning 少——核对 Task 3 的 N 与 `package.json` 里的数字是否一致。

- [ ] **Step 3: 验证 `pnpm check` 端到端通过**

Run: `cd frontend && pnpm check; echo "exit=$?"`

Expected: `exit=0`；依次看到 typecheck → lint → vitest 三段输出，全部绿。

- [ ] **Step 4: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-visible-219
git add frontend/package.json
git commit -m "chore(frontend): add lint/lint:fix scripts with baseline N=<N>

将 Task 3 dry-run 得到的 warning 总数锁为 --max-warnings 基线。
pnpm check 现在串联 typecheck + lint + vitest。"
```

---

## Task 5: 在 CI workflow 加 Lint step

**Files:**
- Modify: `.github/workflows/test.yml:52-59` (frontend-tests job 的 steps)

- [ ] **Step 1: 修改 `.github/workflows/test.yml` 的 `frontend-tests` job**

把 `Type check` step 之后、`Run tests` step 之前的区域：

```yaml
      - name: Type check
        working-directory: frontend
        run: pnpm typecheck

      - name: Run tests
        working-directory: frontend
        run: pnpm test:coverage
```

改为：

```yaml
      - name: Type check
        working-directory: frontend
        run: pnpm typecheck

      - name: Lint
        working-directory: frontend
        run: pnpm lint

      - name: Run tests
        working-directory: frontend
        run: pnpm test:coverage
```

- [ ] **Step 2: 用 yaml 语法检查器验证 workflow 合法**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo "yaml OK"`

Expected: 输出 `yaml OK`。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci(frontend): add Lint step to frontend-tests job

在 Type check 后、Run tests 前运行 pnpm lint。
独立 step 让 GitHub Actions UI 中 typecheck/lint/test 分开着色。"
```

---

## Task 6: 在 `CONTRIBUTING.md` 添加前端 ESLint 文档

**Files:**
- Modify: `CONTRIBUTING.md:43-54` (代码质量章节)

**说明：** 在现有「**Lint & Format（ruff）：**」段落之后、「**测试覆盖率：**」段落之前插入新的「**Lint（前端 ESLint）：**」段落。

- [ ] **Step 1: 修改 `CONTRIBUTING.md`，在代码质量章节 ruff 段落之后插入前端 ESLint 段落**

把：

```markdown
## 代码质量

**Lint & Format（ruff）：**

```bash
uv run ruff check . && uv run ruff format .
```

- 规则集：`E`/`F`/`I`/`UP`，忽略 `E402` 和 `E501`
- line-length：120
- CI 中强制检查：`ruff check . && ruff format --check .`

**测试覆盖率：**
```

改为：

````markdown
## 代码质量

**Lint & Format（ruff）：**

```bash
uv run ruff check . && uv run ruff format .
```

- 规则集：`E`/`F`/`I`/`UP`，忽略 `E402` 和 `E501`
- line-length：120
- CI 中强制检查：`ruff check . && ruff format --check .`

**Lint（前端 ESLint）：**

```bash
cd frontend && pnpm lint          # 检查
cd frontend && pnpm lint:fix      # 自动修可修的部分
```

- 配置：`frontend/eslint.config.js`（flat config）
- 规则集：`typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- typed linting 启用 `projectService: true`，能检查 `no-floating-promises`、`no-misused-promises` 等 async 相关问题
- CI 中强制检查：`frontend-tests` job 的 `Lint` step

**baseline ratchet（--max-warnings）：**

项目处于 a11y 工程化迁移期。`package.json` 的 `"lint"` 脚本里 `--max-warnings=<N>` 锁住历史未修的 warning 总数：

- N > 0 时，CI 只允许 warning ≤ N（新增 warning 会失败）
- 修复 warning 后须**同步下调** N 数字；不允许上调
- 修完一类 rule 后，从 `eslint.config.js` 的 `MIGRATION_WARN_RULES` 中删除对应条目（该 rule 自动回到 error 级别）
- 目标：N 最终降到 0，移除 `--max-warnings` 参数

**PR 2 / PR 3 作者的操作清单：**

1. 本地 `cd frontend && pnpm lint` 看当前 warning 数
2. 修复 warning 直到数字下降
3. 更新 `package.json` 里 `--max-warnings=<N>` 为当前数字
4. 删掉 `eslint.config.js` 里 `MIGRATION_WARN_RULES` 中已清零的 rule 条目
5. 提交，CI 验证

CR checklist：**`--max-warnings` 数字在 diff 里只允许减不允许加**。

**本地 IDE 建议（不提交 repo）：**

`.vscode/` 已在 `.gitignore`。自行添加 `frontend/.vscode/settings.json` 可让 VS Code/Cursor 实时显示 lint 黄线并在保存时自动修复：

```json
{
  "eslint.workingDirectories": [{ "pattern": "./frontend" }],
  "editor.codeActionsOnSave": { "source.fixAll.eslint": "explicit" }
}
```

**已知约束：** ESLint 锁在 v9 系列，因为 `eslint-plugin-react-hooks@7` 的 peer dependency 尚未支持 ESLint v10。待插件更新后独立升级。

**测试覆盖率：**
````

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs(contributing): 新增前端 ESLint 指南

覆盖本地使用、规则集、baseline ratchet 机制、
PR 作者操作清单、IDE 建议、已知约束（v9 锁定）。"
```

---

## Task 7: 最终端到端验证与 diff 审阅

**Files:** 无改动（只做验证）

- [ ] **Step 1: 确认 `src/**` 无任何改动**

Run: `git diff main...HEAD --stat | grep 'frontend/src/' || echo "OK: no src/** changes"`

Expected: 输出 `OK: no src/** changes`。

**如果** 输出了任何 `frontend/src/` 开头的文件，说明违反「零业务代码改动」约束——检查是哪个 task 误改，revert 回来再补做。

- [ ] **Step 2: 完整跑 `pnpm check`**

Run: `cd frontend && pnpm check; echo "exit=$?"`

Expected: `exit=0`；输出 typecheck → lint（`0 errors, <N> warnings`）→ vitest 全绿。

- [ ] **Step 3: 检查最终 diff 的文件清单**

Run: `git diff main...HEAD --name-only`

Expected: 仅包含以下 6 个文件（顺序不限）：

```
.github/workflows/test.yml
CONTRIBUTING.md
docs/superpowers/plans/2026-04-13-frontend-eslint-jsx-a11y.md
docs/superpowers/specs/2026-04-13-frontend-eslint-jsx-a11y-design.md
frontend/eslint.config.js
frontend/package.json
frontend/pnpm-lock.yaml
```

**如果有额外文件**：全数审阅是否属于本 PR 范围；spec / plan 文件（docs/superpowers/*）本身是可接受的。

- [ ] **Step 4: push 到远程并跑 CI**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/fix/a11y-focus-visible-219
git push -u origin fix/a11y-focus-visible-219
```

Expected: 推送成功；GitHub Actions 触发 `frontend-tests` 和 `backend-tests` 两个 job。

- [ ] **Step 5: 监控 CI 结果**

Run: `gh run watch --repo ArcReel/ArcReel` （或 `gh pr checks <pr-number>` 若已创建 PR）

Expected: `frontend-tests` job 全绿，`Lint` step 可见且通过（耗时 15-40s）。

**如果 `Lint` step 挂**：本地 `pnpm lint` 应复现。常见原因：(a) CI 的 Node 版本和本地不一致导致 lint 结果不同；(b) `projectService: true` 在 CI 上解析 tsconfig 的方式有差异——退回旧 `parserOptions: { project: './tsconfig.json' }` 显式路径。

- [ ] **Step 6: （可选）创建 PR**

```bash
gh pr create --title "chore(frontend): introduce ESLint + jsx-a11y (#219)" --body "$(cat <<'EOF'
## Summary

- 引入前端 ESLint 工具链（eslint v9.39.4 + typescript-eslint v8 + react/react-hooks/jsx-a11y 插件）
- flat config + typed linting（`projectService: true`）
- `--max-warnings=<BASELINE>` ratchet 机制锁住既有违规为 warning
- CI 新增 `Lint` step
- CONTRIBUTING.md 补充前端 lint 指南
- **零 `src/**` 业务代码改动**

Part of #219（issue 分成 3 个 PR：本 PR 为基础设施；PR 2 做 focus-visible + 3 类 ARIA 修复；PR 3 扫尾全部 warning）。

Spec: `docs/superpowers/specs/2026-04-13-frontend-eslint-jsx-a11y-design.md`

## Test plan

- [x] 本地 `cd frontend && pnpm install` 无 peer 冲突
- [x] 本地 `cd frontend && pnpm check` 全绿（typecheck + lint + vitest）
- [x] `git diff main...HEAD --stat` 无 `frontend/src/` 改动
- [ ] GitHub Actions frontend-tests / backend-tests 全绿
- [ ] 新 `Lint` step 在 GHA UI 中可见且通过
EOF
)"
```

Expected: 输出 PR URL；保留给 review。

---

## Self-Review Notes（完成于 plan 写作时）

- **Spec coverage 检查**：spec 「范围 / PR 1 做的事」6 项 → Task 1 (依赖) / Task 2 (eslint.config.js) / Task 4 (package.json scripts) / Task 5 (test.yml) / Task 6 (CONTRIBUTING.md) / Task 1 (pnpm-lock.yaml) 各覆盖一项。spec「ESLint 配置」代码块与 Task 2 Step 1 内容对齐，除一处修正：ignores 从 `*.config.js` 扩展到 `**/*.config.*`，以覆盖 `vite.config.ts` / `vitest.config.ts`（Task 2 说明里解释了原因）。
- **Placeholder 扫描**：`<N>` 在 Task 4 明确要求以 Task 3 实际数字替换，未留 TBD/TODO。
- **Type consistency**：`MIGRATION_WARN_RULES` 常量名、`projectService: true` flag 在 Task 2 / Task 3 / Task 6 一致。`pnpm exec eslint` 调用方式统一（Task 2 Step 2、Task 3 Step 1/4、Task 3 Step 2）。
- **tsconfig 兼容性**：`frontend/tsconfig.json` include 为 `["src/**/*"]`，和 eslint.config.js 的 `files: ["src/**/*.{ts,tsx}"]` 对齐，typed linting 不会扫到 tsconfig 之外的文件。
