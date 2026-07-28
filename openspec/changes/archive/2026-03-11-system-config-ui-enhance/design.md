## Context

当前 `SystemConfigPage.tsx`（约 1389 行）采用两个顶栏 Tab："config"（全部配置混合）和 "api-keys"。所有配置字段堆在单一 config Tab 内，保存按钮固定在页面底部。主要问题：

- 配置字段无明确分类，用户难以找到所需项
- 仅部分字段（如 API key）有清除按钮，`base_url` 等字段无法快速清除
- 保存按钮在页面底部，用户编辑上方字段时不知道需要点击保存
- 组件过大，维护困难

## Goals / Non-Goals

**Goals:**
- 将顶栏 Tab 从 `[config, api-keys]` 扩展为 `[ArcReel 智能体配置, AI 生图/生视频配置, 高级配置, API Keys]`，每个配置 Tab 承载对应分类的字段
- 每个配置 Tab 有独立保存按钮；Tab 内有未保存变更时，保存页脚以 sticky 方式固定在视口底部
- 为所有可选字段统一添加清除（×）按钮
- 使用 `/frontend-design` 技能进行 UI 设计，使用 `/vercel-react-best-practices` 技能进行开发

**Non-Goals:**
- 不修改后端 API 或数据类型定义
- 不改变现有配置的语义或默认值
- 不引入新的外部依赖

## Decisions

### 决策 1：Tab 结构设计

**选择**：四个顶栏 Tab，原 `api-keys` Tab 保留不变，原 `config` Tab 拆分为三个

| Tab | 内容 |
|-----|------|
| ArcReel 智能体配置 | Anthropic API Key、Base URL、各模型选择 |
| AI 生图/生视频配置 | Gemini API Key、Base URL、后端选择、模型选择、Vertex 凭证 |
| 高级配置 | 速率限制（RPM）、请求间隔、最大并发 Worker 数 |
| API Keys | 现有 ApiKeysTab 组件，不变 |

**理由**：Tab 比 card 分组层级更高，视觉上分类更清晰；每个 Tab 独立聚焦，内容更简洁；保留现有 API Keys Tab 结构不变，降低改动范围。

### 决策 2：未保存变更感知 — Sticky 保存页脚

**核心问题**：Tab 内内容可能较长，用户编辑上方字段后不知道需要点击保存按钮。

**选择**：每个配置 Tab 的保存页脚在有未保存变更时变为 sticky，固定在视口底部。

**交互细节**：
- 无未保存变更时：保存页脚正常渲染在 Tab 内容底部（非 sticky），save 按钮为禁用态
- 有未保存变更时：保存页脚变为 `position: sticky; bottom: 0`，保存按钮高亮（primary 色），Tab 标签旁出现小圆点徽标
- 保存中：按钮显示加载态，禁用输入
- 保存成功：sticky 解除，页脚回到内容区

```typescript
// Tab 内部
const isDirty = !deepEqual(draft, savedValues)

<div className={cn(
  "border-t p-4 flex items-center justify-between bg-background",
  isDirty && "sticky bottom-0 z-10 shadow-[0_-2px_8px_rgba(0,0,0,0.08)]"
)}>
  {isDirty && <span className="text-sm text-muted-foreground">有未保存的更改</span>}
  <div className="flex gap-2 ml-auto">
    {isDirty && <Button variant="ghost" onClick={handleReset}>撤销</Button>}
    <Button disabled={!isDirty || saving} onClick={handleSave}>
      {saving ? <Spinner /> : "保存"}
    </Button>
  </div>
</div>
```

### 决策 3：状态模型

**选择**：每个配置 Tab 组件内部维护自己的草稿状态（`useState`），Tab 间完全隔离

```typescript
type TabStatus = "idle" | "saving" | "error"

// 每个 Tab 组件内
const [draft, setDraft] = useState<AgentDraft>(buildDraft(config))
const [status, setStatus] = useState<TabStatus>("idle")
const savedRef = useRef(draft)
const isDirty = !deepEqual(draft, savedRef.current)
```

**理由**：Tab 间状态隔离，切换 Tab 不影响其他 Tab 的未保存变更；每个 Tab 组件自包含，易于测试。

### 决策 4：组件结构

```
SystemConfigPage
├── TopTabs (顶栏 Tab 导航)
│   ├── Tab: agent      → AgentConfigTab
│   ├── Tab: media      → MediaConfigTab
│   ├── Tab: advanced   → AdvancedConfigTab
│   └── Tab: api-keys   → ApiKeysTab (不变)
└── TabSaveFooter (可复用，每个配置 Tab 底部)
```

**Tab 标签**：有未保存变更时在 Tab 名称旁显示小圆点 `●`，提醒用户。

### 决策 5：必填配置缺失检测与提示

**必填项定义**：以下三项均需满足，系统才能正常运行：

1. **ArcReel 智能体 API Key**：`anthropic_api_key.is_set === true`
2. **AI 生图后端凭证**：取决于 `image_backend` 的值：
   - `"aistudio"` → `gemini_api_key.is_set === true`
   - `"vertex"` → `vertex_credentials.is_set === true`
3. **AI 生视频后端凭证**：取决于 `video_backend` 的值：
   - `"aistudio"` → `gemini_api_key.is_set === true`
   - `"vertex"` → `vertex_credentials.is_set === true`

注：`image_backend` 与 `video_backend` 相互独立，可分别使用不同的后端提供商；`gemini_api_key` 和 `vertex_credentials` 由两者共享（同一套凭证）。

**检测函数**：

```typescript
function checkBackendCredential(backend: SystemBackend, config: SystemConfigView): boolean {
  return backend === "aistudio"
    ? config.gemini_api_key.is_set
    : config.vertex_credentials.is_set
}

function getConfigIssues(config: SystemConfigView): ConfigIssue[] {
  const issues: ConfigIssue[] = []
  if (!config.anthropic_api_key.is_set)
    issues.push({ key: "anthropic", tab: "agent", label: "ArcReel 智能体 API Key（Anthropic）未配置" })
  if (!checkBackendCredential(config.image_backend, config))
    issues.push({ key: "image", tab: "media",
      label: config.image_backend === "aistudio"
        ? "AI 生图 API Key（Gemini AI Studio）未配置"
        : "AI 生图 Vertex AI 凭证未上传" })
  if (!checkBackendCredential(config.video_backend, config))
    issues.push({ key: "video", tab: "media",
      label: config.video_backend === "aistudio"
        ? "AI 生视频 API Key（Gemini AI Studio）未配置"
        : "AI 生视频 Vertex AI 凭证未上传" })
  // 去重：image 和 video 指向同一 tab 且原因相同时合并
  return dedupIssues(issues)
}
```

`SecretFieldView.is_set` 和 `VertexCredentialView.is_set` 由后端直接提供，无需前端解析掩码格式。

**提示位置与形式**：

| 位置 | 提示形式 |
|------|---------|
| `ProjectsPage.tsx` 右上角设置按钮 | 红色圆点徽标叠加在 Settings 图标上 |
| `GlobalHeader.tsx` 右上角设置按钮 | 同上 |
| 设置页顶部（Tab 导航上方） | 黄色警告横幅，列出每条缺失原因并提供点击跳转到对应 Tab 的链接 |

**数据共享**：配置完整性状态通过 `useConfigStatus` hook（Zustand 或 React Context）全局共享，`ProjectsPage` 与 `GlobalHeader` 读取同一份缓存，避免重复请求。

**缓存策略**：应用初始化（AuthGuard 通过后）请求一次；配置 Tab 保存成功后重新检测。

## Risks / Trade-offs

- **切换 Tab 时未保存变更丢失** → Tab 标签上的圆点徽标提醒用户；可选：切换时弹出确认对话框 → 先用徽标方案，避免过度打扰
- **Sticky 页脚遮挡最后一行** → 页面底部添加足够 `padding-bottom`；sticky 解除后自动恢复 → 低风险
- **重构范围较大** → `SystemConfigPage.tsx` 需大幅重写 → 分阶段：先拆分 Tab 结构，再加 sticky 感知
- **配置完整性请求时机** → 应用初始化时若未登录则无法请求 → 配置完整性检测放在 AuthGuard 通过后执行，未登录时不显示徽标
