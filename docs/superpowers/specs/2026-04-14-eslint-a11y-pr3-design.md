# 前端 ESLint + jsx-a11y 扫尾（PR 3）

- **日期**：2026-04-14
- **对应 Issue**：#219 (a11y audit)
- **PR 标题**（建议）：`chore(frontend): clean up remaining ESLint warnings and flip rules to error`
- **作者**：Pollo

## 背景

PR 1 (#290) 建立了前端 ESLint + jsx-a11y 基础设施，使用 `--max-warnings=212` ratchet 与三个 `MIGRATION_WARN_RULES_*` 常量把既有违规固化为 warning。PR 2 (#292) 正在修复 focus-ring / textarea label / progressbar ARIA / file input aria-label 四类主题问题，warning 数维持在 211 左右。

本 PR（PR 3）是该系列的收尾：**把剩余所有 warning 清零，把所有 rule flip 到 error，移除 `--max-warnings` 参数**。

### 实测 warning 分布（211 tracked + 18 未入账 = 229 条）

| 类别 | 规则 | 数量 |
|---|---|---|
| **Typed (151)** | no-misused-promises | 37 |
| | no-unsafe-member-access | 26 |
| | no-unnecessary-type-assertion | 17 |
| | no-unsafe-assignment | 16 |
| | no-floating-promises | 16 |
| | no-unsafe-return | 12 |
| | no-unsafe-argument | 8 |
| | no-unsafe-call | 5 |
| | no-redundant-type-constituents | 2 |
| | require-await | 1 |
| **ALL / React-Hooks (40)** | react-hooks/set-state-in-effect | 18 |
| | react-hooks/exhaustive-deps ⚠️ | 17 |
| | no-explicit-any | 9 |
| | react-hooks/refs | 6 |
| | no-unused-vars | 5 |
| | no-unsafe-finally | 1 |
| | react-hooks/incompatible-library ⚠️ | 1 |
| **A11y (16)** | no-static-element-interactions | 5 |
| | no-autofocus | 5 |
| | click-events-have-key-events | 3 |
| | media-has-caption | 2 |
| | no-noninteractive-element-interactions | 1 |

⚠️ 标记的两条（`react-hooks/exhaustive-deps`、`react-hooks/incompatible-library`）**不在** PR 1 填充的任何 `MIGRATION_WARN_RULES_*` 中，是 PR 1 dry-run 的覆盖盲区，需本 PR 一并处理。

## 范围

### PR 3 做的事（Do）

1. **类型源头加类型**：从 API client（`api/auth.ts`、`api/tasks.ts` 等）、SSE payload（`useTasksSSE.ts`、`useProjectEventsSSE.ts`）、i18n 等源头一次性加类型，消除 `any` 向下游扩散
2. **新增 `frontend/src/utils/async.ts`** 导出 `voidPromise(fn, onError?)` 和 `voidCall(promise, onError?)` helper，统一替换 `onClick={async ...}` 和 fire-and-forget 调用
3. **React Hooks 42 条**按"真 bug 按规则修 / 有意为之加 `// eslint-disable-next-line X -- <中文理由>`"两条路径逐条处理
4. **a11y 16 条**：`<div onClick>` 家族优先换 `<button>`、否则用 `activateOnEnterSpace` helper 三件套；`autoFocus` 全删改用 `useAutoFocus` hook；`media-has-caption` 对生成预览视频加 disable + 理由
5. **测试文件 override**：`src/**/*.test.{ts,tsx}` 和 `src/test/**` 整体关闭 `no-explicit-any` 与 6 条 `no-unsafe-*`
6. **未入账规则严格化**：config 末尾显式 `"error"` 设置 `react-hooks/exhaustive-deps` + `react-hooks/incompatible-library`
7. **清空 migration 基础设施**：删除 `MIGRATION_WARN_RULES_TYPED` / `MIGRATION_WARN_RULES_A11Y` / `MIGRATION_WARN_RULES_ALL` 三个常量定义和展开 block；删除 PR 2/3 操作指引注释
8. **移除 ratchet**：`package.json` 的 `lint` script 去掉 `--max-warnings=212`
9. **文档**：`CONTRIBUTING.md` 新增「ESLint disable 使用规范」小节
10. **PR 描述**：列出所有新增 `eslint-disable-*` 的表格（rule × file:line × 理由）

### PR 3 不做的事（Don't）

- ❌ React Hooks 语义之外的重构（即便顺手）—— 单处修复超 10 行改动改走 disable + TODO，超出 3 条则拆独立工单
- ❌ 改 i18n 文案、UI 样式（除非 a11y 修复必须）
- ❌ 调 ESLint 工具链版本（留独立工单）
- ❌ 引入新的 notification/toast 基建 —— `voidPromise` 默认只用 `console.error`，`onError` 参数为未来升级预留

### 依赖顺序

PR 2 (#292) merge 后 rebase 本 PR → 基于 rebase 后的实际 warning 清单重算改动 → 清零 → 移除 ratchet。如 PR 2 已修复的条目在本 PR 基底中不存在，直接跳过。

## 技术决策

### 1. 类型源头改造（对应 unsafe-* 86 + no-explicit-any 9 + 冗余断言 20 = ~115 条）

**策略**：从 3 个 `any` 源头一次性加类型，下游 `unsafe-*` 报警自愈。

**目标文件 + 类型定义**：

**`frontend/src/api/auth.ts`**

```ts
interface LoginResponse { access_token: string; token_type: string }
interface ErrorResponse { detail: string }

export async function login(...): Promise<LoginResponse> { ... }
```

错误分支用 type guard 窄化 `catch (e: unknown)` 读 `detail`。自愈 `LoginPage.tsx:36/37/40/41` 的 6 条 unsafe-*。

**`frontend/src/api/tasks.ts` + `useTasksSSE.ts`**

```ts
interface TaskEvent { type: "stats"; stats: TaskStats }
// 解析：JSON.parse(e.data) as TaskEvent + runtime type guard（校验 type 字段）
```

自愈 `useTasksSSE.ts:33-34` 的 4 条 unsafe-*。

**`frontend/src/hooks/useProjectEventsSSE.ts`**

```ts
type ProjectEvent =
  | { type: "project_updated"; project_name: string }
  | { type: "task_status"; task_id: string; status: string }
  | ...;
```

按 `type` 字段做 discriminated union 窄化。

**`api/` 其他 `any` 消除**：逐文件搜 `: any` / `as any` / 未类型化 `JSON.parse`，对照 `server/routers/*` 的 Pydantic response model 镜像前端 interface（不引入共享 schema 生成器）。

**`no-unnecessary-type-assertion` (17 条)**：源头加类型后断言自然冗余，删除；少量 `as const` 保留。

**`no-redundant-type-constituents` (2 条) / `require-await` (1 条)**：按行判断。

### 2. 测试文件 override

`eslint.config.js` 新增：

```js
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
}
```

覆盖 `stores.test.ts`、`useTasksSSE.test.tsx`、`src/test/setup.ts` 等测试层 `any`。

### 3. Promise 处理 helper（对应 ~54 条）

**新增 `frontend/src/utils/async.ts`**：

```ts
type VoidPromiseOptions = {
  onError?: (err: unknown) => void;
};

export function voidPromise<Args extends unknown[]>(
  fn: (...args: Args) => Promise<unknown>,
  opts?: VoidPromiseOptions,
): (...args: Args) => void {
  return (...args) => {
    fn(...args).catch((err: unknown) => {
      if (opts?.onError) opts.onError(err);
      else console.error(err);
    });
  };
}

export function voidCall<T>(
  promise: Promise<T>,
  onError: (err: unknown) => void = console.error,
): void {
  promise.catch(onError);
}
```

**使用模式**：

| 场景 | 当前写法 | 目标写法 |
|---|---|---|
| onClick 绑 async | `onClick={async () => { await save() }}` | `onClick={voidPromise(save)}` |
| fire-and-forget | `fetchData()` 裸调用 | `voidCall(fetchData())` |
| 顶层初始化 | `i18n.init()` | `voidCall(i18n.init())` |
| SSE onmessage | `es.onmessage = async (e) => {...}` | `es.onmessage = (e) => voidCall(handleMessage(e))` |

**单元测试**：`frontend/src/utils/async.test.ts` 覆盖 4 条路径（正常调用、默认 onError、自定义 onError、参数透传）。

### 4. React Hooks 语义（对应 42 条，加杂项 6 条 = 48 条）

**原则**：逐条判断，能按规则修的按规则修，有意为之用 `// eslint-disable-next-line <rule> -- <中文理由>`。目标 disable 比例 ≤60%。

**`react-hooks/set-state-in-effect` (18 条)** — 模式判断：

| 常见模式 | 动作 |
|---|---|
| 派生态 `useEffect(() => setFoo(derive(p)), [p])` | 改 `useMemo` |
| 同步受控态副本（`if (data) setLocalCopy(data)`） | 改 `key={data.id}` + 初始化 `useState(data)`；若必须同步，disable + 理由 |
| Mount-only 初始化信号 | 如确为 mount signal，disable + 理由 |

**`react-hooks/exhaustive-deps` (17 条)** — 模式判断：

| 常见模式 | 动作 |
|---|---|
| 缺稳定 setter（React `setX` / wouter `setLocation`） | 优先加依赖（零成本）；若加依赖触发重订阅，disable + 理由 |
| Mount-only effect 故意漏依赖 | disable + 理由"mount-only 初始化，刻意不追随 X 变化" |

**`react-hooks/refs` (6 条)** — render 阶段访问 `ref.current` 移到 useEffect 或事件回调。

**`react-hooks/incompatible-library` (1 条)** — case-by-case。

**硬约束**：单处修复超过 10 行改动时，改走 disable + `// TODO(a11y-pr3): 后续重构` 注释，并在 PR body 特别列出，上限 3 条；超出则拆独立工单。

**`no-unsafe-finally` (1)** + **`no-unused-vars` (5)**：单点机械修复，`no-unsafe-finally` 定位 `useProjectEventsSSE.ts:175`。

### 5. a11y 扫尾（对应 16 条）

**`<div onClick>` 家族 (9 条)**：

- **可换 `<button>`**（无嵌套交互、无特殊 DOM 语义）→ 换为 `<button type="button" className="appearance-none bg-transparent p-0 text-left ...">` 保持视觉
- **不能换**（含嵌套交互、语义是 row/list item）→ 加三件套 + 复用 helper

**新增 `frontend/src/utils/a11y.ts`**：

```ts
export function activateOnEnterSpace(handler: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handler();
    }
  };
}
```

调用点：`<div role="button" tabIndex={0} onClick={handler} onKeyDown={activateOnEnterSpace(handler)}>`。

**`no-autofocus` (5 条)** — 新增 `frontend/src/hooks/useAutoFocus.ts`：

```ts
export function useAutoFocus<T extends HTMLElement>(enabled = true) {
  const ref = useRef<T>(null);
  useEffect(() => { if (enabled) ref.current?.focus(); }, [enabled]);
  return ref;
}
```

5 处 `autoFocus` 删 prop，改 `const inputRef = useAutoFocus<HTMLInputElement>(); <input ref={inputRef} />`。

**`media-has-caption` (2 条)**：

```tsx
{/* eslint-disable-next-line jsx-a11y/media-has-caption -- 生成式预览视频暂无字幕源，将来如引入字幕生成则移除此 disable */}
<video src={...} controls />
```

### 6. 未入账规则严格化

`eslint.config.js` 末尾新增：

```js
// 本项目严于 recommended：exhaustive-deps / incompatible-library 一律视为 error
{
  rules: {
    "react-hooks/exhaustive-deps": "error",
    "react-hooks/incompatible-library": "error",
  },
},
```

### 7. `eslint-disable-*` 使用规范

`CONTRIBUTING.md` 前端章节新增「ESLint disable 使用规范」：

- 形式：`// eslint-disable-next-line <rule> -- <中文理由>`，**理由强制**
- **禁用**：`/* eslint-disable */` 文件级、`// eslint-disable-line` 无理由、`@ts-ignore` 联用
- **PR 描述要求**：新增的 disable 必须在 PR body 以表格列出 `rule | file:line | 理由`
- **文件级关闭**只允许通过 `eslint.config.js` 的 `files` override，且须在 config 注释说明原因
- 不可接受的理由：「太麻烦」「暂时这样」「later fix」
- 可接受的理由示例：「React setter 引用稳定」「mount-only 初始化」「生成式预览视频无字幕源」

## 最终 `eslint.config.js` 结构

```js
// 1. 全局 ignores
// 2. js.configs.recommended
// 3. tseslint.configs.recommendedTypeChecked
// 4. react flat recommended + jsx-runtime
// 5. react-hooks recommended
// 6. jsx-a11y recommended
// 7. src/** typed linting language options
// 8. 测试文件 override（off no-explicit-any + 6 条 unsafe-*）
// 9. 测试文件语言选项 override（沿用 PR 1）
// 10. 严格化 override（exhaustive-deps + incompatible-library = error）
```

**删除**：`MIGRATION_WARN_RULES_*` 三个常量定义、`...MIGRATION_WARN_RULES_*` 展开的 rules block、PR 2/3 操作指引段注释。

**`package.json` 变更**：

```diff
- "lint": "eslint . --max-warnings=212",
+ "lint": "eslint .",
```

`lint:fix` 保持不变。CI `.github/workflows/test.yml` 无需改动（命令未变，语义自动从"不超过 212"变成"零违规"）。

## 交付物清单

**新增文件**：
- `frontend/src/utils/async.ts` + `async.test.ts`
- `frontend/src/utils/a11y.ts`
- `frontend/src/hooks/useAutoFocus.ts`

**修改文件**（估 30-45 个）：
- `frontend/eslint.config.js`
- `frontend/package.json`
- `frontend/src/api/auth.ts`、`api/tasks.ts` 等 API 源头
- `frontend/src/hooks/useTasksSSE.ts`、`useProjectEventsSSE.ts`
- `frontend/src/pages/LoginPage.tsx`
- `frontend/src/i18n/index.ts`、`src/test/setup.ts`
- 其余散落 onClick / set-state-in-effect / div onClick 命中文件
- `CONTRIBUTING.md`

## 验证流程

1. `pnpm lint` 输出 `0 problems`
2. `pnpm typecheck` 零 error
3. `pnpm test` 现有测试全过 + 新增 `async.test.ts` 通过
4. `pnpm build` 通过
5. 浏览器手动验：登录页 autoFocus、代表性 onClick 交互、SSE 连接正常（任务卡片状态流、项目事件流）
6. CI frontend-tests job green

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| PR 2 (#292) merge 延后导致 rebase 冲突扩大 | 中 | rebase 时全量重跑 `pnpm lint`，用实际清单校正；PR 2 已修条目直接跳过 |
| 类型源头加类型引发下游 TS error 瀑布 | 中 | 每改完一个源头立即 `pnpm typecheck`；源头类型与后端 Pydantic 对齐 |
| React Hooks 按规则修改变行为（如加依赖触发额外 effect） | 中-高 | 每处修复点 git commit 粒度细；不确定时优先 disable + 理由；浏览器手动跑 SSE、路由关键流 |
| `voidPromise` 替换丢失原 async 错误 UI 反馈 | 低-中 | helper 默认 `console.error` 不静默；原代码多数 `async () =>` 本就吞错，替换后同等或更好 |
| 新增 disable 过多被 review 拒 | 低 | 目标比例 ≤60%，超限则 hook 语义问题拆独立工单 |

## 回滚策略

- **纯规则回归**：revert `eslint.config.js` + `package.json` 即可（恢复 `--max-warnings=212` 和 migration 常量）
- **代码回归**：Git revert 整个 merge commit

## 交叉引用

- PR 1 spec：`docs/superpowers/specs/2026-04-13-frontend-eslint-jsx-a11y-design.md`
- PR 2 spec：`docs/superpowers/specs/2026-04-13-a11y-focus-ring-pr2-design.md`
- Issue：#219
