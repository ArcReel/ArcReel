## Context

The current `SystemConfigPage.tsx` (approximately 1389 lines) uses two top navigation tabs: "config" (all configuration mixed together) and "api-keys". All configuration fields are piled into the single config tab, with the save button fixed at the bottom of the page. Main issues:

- Configuration fields have no clear categorization, making it hard for users to find what they need
- Only some fields (e.g., API keys) have clear buttons; fields like `base_url` cannot be quickly cleared
- The save button is at the bottom of the page; users editing fields at the top don't realize they need to click save
- The component is too large and difficult to maintain

## Goals / Non-Goals

**Goals:**
- Extend top navigation tabs from `[config, api-keys]` to `[ArcReel Agent Configuration, AI Image/Video Generation Configuration, Advanced Configuration, API Keys]`, with each configuration tab containing the corresponding categorized fields
- Each configuration tab has an independent save button; when there are unsaved changes in a tab, the save footer is fixed at the bottom of the viewport in a sticky manner
- Add clear (×) buttons uniformly to all optional fields
- Use the `/frontend-design` skill for UI design and `/vercel-react-best-practices` for development

**Non-Goals:**
- Not modifying backend API or type definitions
- Not changing the semantics or default values of existing configuration
- Not introducing new external dependencies

## Decisions

### Decision 1: Tab Structure Design

**Choice**: Four top navigation tabs; original `api-keys` tab retained unchanged; original `config` tab split into three

| Tab | Content |
|-----|---------|
| ArcReel Agent Configuration | Anthropic API Key, Base URL, individual model selections |
| AI Image/Video Generation Configuration | Gemini API Key, Base URL, backend selection, model selection, Vertex credentials |
| Advanced Configuration | Rate limit (RPM), request interval, maximum concurrent worker count |
| API Keys | Existing ApiKeysTab component, unchanged |

**Rationale**: Tabs provide a higher hierarchy than card groupings, making categories visually clearer; each tab is independently focused with simpler content; retaining the existing API Keys tab structure reduces the change scope.

### Decision 2: Unsaved Change Awareness — Sticky Save Footer

**Core issue**: Tab content may be long; users editing upper fields don't know they need to click the save button.

**Choice**: Each configuration tab's save footer becomes sticky when there are unsaved changes, fixed at the bottom of the viewport.

**Interaction details**:
- No unsaved changes: save footer renders normally at the bottom of the tab content (non-sticky); save button is disabled
- Has unsaved changes: save footer becomes `position: sticky; bottom: 0`; save button is highlighted (primary color); a small dot appears next to the tab label
- Saving: button shows loading state; inputs disabled
- Save successful: sticky released; footer returns to content area

```typescript
// Inside the tab
const isDirty = !deepEqual(draft, savedValues)

<div className={cn(
  "border-t p-4 flex items-center justify-between bg-background",
  isDirty && "sticky bottom-0 z-10 shadow-[0_-2px_8px_rgba(0,0,0,0.08)]"
)}>
  {isDirty && <span className="text-sm text-muted-foreground">Unsaved changes</span>}
  <div className="flex gap-2 ml-auto">
    {isDirty && <Button variant="ghost" onClick={handleReset}>Revert</Button>}
    <Button disabled={!isDirty || saving} onClick={handleSave}>
      {saving ? <Spinner /> : "Save"}
    </Button>
  </div>
</div>
```

### Decision 3: State Model

**Choice**: Each configuration tab component maintains its own draft state internally (`useState`); tabs are completely isolated

```typescript
type TabStatus = "idle" | "saving" | "error"

// Inside each tab component
const [draft, setDraft] = useState<AgentDraft>(buildDraft(config))
const [status, setStatus] = useState<TabStatus>("idle")
const savedRef = useRef(draft)
const isDirty = !deepEqual(draft, savedRef.current)
```

**Rationale**: Tab state is isolated; switching tabs does not affect other tabs' unsaved changes; each tab component is self-contained and easy to test.

### Decision 4: Component Structure

```
SystemConfigPage
├── TopTabs (top navigation tab navigation)
│   ├── Tab: agent      → AgentConfigTab
│   ├── Tab: media      → MediaConfigTab
│   ├── Tab: advanced   → AdvancedConfigTab
│   └── Tab: api-keys   → ApiKeysTab (unchanged)
└── TabSaveFooter (reusable, at the bottom of each configuration tab)
```

**Tab labels**: When there are unsaved changes, a small dot `●` appears next to the tab name to remind the user.

### Decision 5: Missing Required Configuration Detection and Warning

**Required items definition**: All three of the following must be satisfied for the system to function properly:

1. **ArcReel Agent API Key**: `anthropic_api_key.is_set === true`
2. **AI image generation backend credentials**: depends on the value of `image_backend`:
   - `"aistudio"` → `gemini_api_key.is_set === true`
   - `"vertex"` → `vertex_credentials.is_set === true`
3. **AI video generation backend credentials**: depends on the value of `video_backend`:
   - `"aistudio"` → `gemini_api_key.is_set === true`
   - `"vertex"` → `vertex_credentials.is_set === true`

Note: `image_backend` and `video_backend` are independent of each other and can use different backend providers; `gemini_api_key` and `vertex_credentials` are shared by both (same set of credentials).

**Detection function**:

```typescript
function checkBackendCredential(backend: SystemBackend, config: SystemConfigView): boolean {
  return backend === "aistudio"
    ? config.gemini_api_key.is_set
    : config.vertex_credentials.is_set
}

function getConfigIssues(config: SystemConfigView): ConfigIssue[] {
  const issues: ConfigIssue[] = []
  if (!config.anthropic_api_key.is_set)
    issues.push({ key: "anthropic", tab: "agent", label: "ArcReel Agent API Key (Anthropic) not configured" })
  if (!checkBackendCredential(config.image_backend, config))
    issues.push({ key: "image", tab: "media",
      label: config.image_backend === "aistudio"
        ? "AI Image API Key (Gemini AI Studio) not configured"
        : "AI Image Vertex AI credentials not uploaded" })
  if (!checkBackendCredential(config.video_backend, config))
    issues.push({ key: "video", tab: "media",
      label: config.video_backend === "aistudio"
        ? "AI Video API Key (Gemini AI Studio) not configured"
        : "AI Video Vertex AI credentials not uploaded" })
  // Deduplication: merge when image and video point to the same tab with the same reason
  return dedupIssues(issues)
}
```

`SecretFieldView.is_set` and `VertexCredentialView.is_set` are provided directly by the backend; the frontend does not need to parse masked formats.

**Warning location and form**:

| Location | Warning form |
|----------|-------------|
| `ProjectsPage.tsx` upper right settings button | Red dot badge overlaid on Settings icon |
| `GlobalHeader.tsx` upper right settings button | Same as above |
| Settings page top (above tab navigation) | Yellow warning banner listing each missing required item with links to jump to the corresponding tab |

**Data sharing**: Configuration completeness status is shared globally via `useConfigStatus` hook (Zustand or React Context); `ProjectsPage` and `GlobalHeader` read from the same cache to avoid duplicate requests.

**Caching strategy**: Request once after app initialization (after AuthGuard passes); re-check after a configuration tab saves successfully.

## Risks / Trade-offs

- **Unsaved changes lost when switching tabs** → Dot badge on the tab label reminds the user; optional: show confirmation dialog on switch → using the badge approach first, avoiding over-interruption
- **Sticky footer blocks the last row** → Add sufficient `padding-bottom` at the bottom of the page; automatically restores after sticky is released → low risk
- **Large refactoring scope** → `SystemConfigPage.tsx` requires major rewriting → phased approach: first split the tab structure, then add sticky awareness
- **Timing of configuration completeness request** → If not logged in during app initialization, cannot make the request → Configuration completeness detection executes after AuthGuard passes; no badge displayed when not logged in
