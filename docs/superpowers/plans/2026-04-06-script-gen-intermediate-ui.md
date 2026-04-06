# 剧本生成中间产物 UI 展示与事件通知 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在 Web UI 中查看/编辑 step1 中间产物，step1 生成后自动通知并导航，仅有 step1 的剧集可在侧边栏可见。

**Architecture:** 后端修复 StatusCalculator 的 drama 模式检测、清理 drafts API（移除 step2/step3）并集成事件发射；前端新增 PreprocessingView 组件、TimelineCanvas Tab 切换、侧边栏 segmented 状态展示、SSE 事件处理。

**Tech Stack:** Python/FastAPI (后端)、React 19/TypeScript/Tailwind CSS 4 (前端)、streamdown (Markdown 渲染)

---

## 文件结构

### 修改的文件

| 文件 | 职责 |
|------|------|
| `lib/status_calculator.py` | 修复 drama 模式 step1 检测 |
| `server/routers/files.py` | 清理 step2/step3 映射，集成事件发射 |
| `tests/test_status_calculator.py` | 补充 drama 模式测试 |
| `tests/test_files_router.py` | 更新 drafts API 测试 |
| `frontend/src/types/workspace.ts` | 新增 draft 事件类型 |
| `frontend/src/utils/project-changes.ts` | 新增 draft 实体标签 |
| `frontend/src/hooks/useProjectEventsSSE.ts` | 处理 draft 事件导航 |
| `frontend/src/components/layout/AssetSidebar.tsx` | 展示 segmented 状态剧集 |
| `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` | 新增 Tab 切换 |
| `frontend/src/components/canvas/StudioCanvasRouter.tsx` | 传递 episode 元数据 |

### 新建的文件

| 文件 | 职责 |
|------|------|
| `frontend/src/components/canvas/timeline/PreprocessingView.tsx` | 预处理内容查看/编辑组件 |

---

### Task 1: StatusCalculator — drama 模式 step1 检测

**Files:**
- Modify: `lib/status_calculator.py:88-102`
- Test: `tests/test_status_calculator.py`

- [ ] **Step 1: 写失败测试 — drama 模式 step1 检测**

在 `tests/test_status_calculator.py` 的 `test_load_episode_script` 方法末尾追加：

```python
        # Case 4: drama 模式 — step1_normalized_script.md 存在 → ("segmented", None)
        draft_dir_drama = project_path / "drafts" / "episode_4"
        draft_dir_drama.mkdir(parents=True)
        (draft_dir_drama / "step1_normalized_script.md").write_text("drama draft")
        calc4 = StatusCalculator(_FakePM(project_root, {}, {}))
        status4, script4 = calc4._load_episode_script("demo", 4, "scripts/episode_4.json", content_mode="drama")
        assert status4 == "segmented"
        assert script4 is None

        # Case 5: drama 模式 — 无 step1_normalized_script.md → ("none", None)
        calc5 = StatusCalculator(_FakePM(project_root, {}, {}))
        status5, script5 = calc5._load_episode_script("demo", 5, "scripts/episode_5.json", content_mode="drama")
        assert status5 == "none"
        assert script5 is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_status_calculator.py::TestStatusCalculator::test_load_episode_script -v`
Expected: FAIL — `_load_episode_script() got an unexpected keyword argument 'content_mode'`

- [ ] **Step 3: 实现 — 修改 `_load_episode_script()` 支持 content_mode**

在 `lib/status_calculator.py` 中修改 `_load_episode_script` 方法签名和内部逻辑：

```python
    def _load_episode_script(
        self, project_name: str, episode_num: int, script_file: str, *, content_mode: str = "narration"
    ) -> tuple:
        """加载单集剧本，返回 (script_status, script|None)，避免重复读取文件。
        script_status: 'generated' | 'segmented' | 'none'
        """
        try:
            script = self.pm.load_script(project_name, script_file)
            return "generated", script
        except FileNotFoundError:
            project_dir = self.pm.get_project_path(project_name)
            try:
                safe_num = int(episode_num)
            except (ValueError, TypeError):
                return "none", None
            draft_filename = (
                "step1_segments.md" if content_mode == "narration" else "step1_normalized_script.md"
            )
            draft_file = project_dir / f"drafts/episode_{safe_num}/{draft_filename}"
            return ("segmented" if draft_file.exists() else "none"), None
        except ValueError as e:
            logger.warning(
                "剧本 JSON 损坏或路径无效，跳过状态计算 project=%s file=%s: %s",
                project_name,
                script_file,
                e,
            )
            return "generated", None
```

- [ ] **Step 4: 更新调用方 — 传递 content_mode**

在 `lib/status_calculator.py` 中，`calculate_project_status()` 方法（~第165行）和 `enrich_project()` 方法（~第217行）都需要获取 content_mode 并传递：

在 `calculate_project_status()` 中，在 for 循环之前获取 content_mode：
```python
        content_mode = project.get("content_mode", "narration")
```

然后修改调用处（~第171行）：
```python
                script_status, script = self._load_episode_script(
                    project_name, episode_num, script_file, content_mode=content_mode
                )
```

在 `enrich_project()` 中同样处理（~第217行起）：
```python
        content_mode = project.get("content_mode", "narration")
        episodes_stats = []
        for ep in project.get("episodes", []):
            script_file = ep.get("script_file", "")
            episode_num = ep.get("episode", 0)

            if script_file:
                script_status, script = self._load_episode_script(
                    project_name, episode_num, script_file, content_mode=content_mode
                )
```

- [ ] **Step 5: 运行测试，确认全部通过**

Run: `uv run python -m pytest tests/test_status_calculator.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add lib/status_calculator.py tests/test_status_calculator.py
git commit -m "fix: StatusCalculator 支持 drama 模式 step1 检测"
```

---

### Task 2: 清理 drafts API 并集成事件发射

**Files:**
- Modify: `server/routers/files.py:360-482`
- Test: `tests/test_files_router.py`

- [ ] **Step 1: 写失败测试 — step2/step3 返回 400**

在 `tests/test_files_router.py` 的 `test_source_decode_and_draft_mode_helpers` 方法中，在已有的 `missing_step` 断言之后追加：

```python
            # step2 and step3 should now be invalid
            step2_resp = client.get("/api/v1/projects/demo/drafts/1/step2")
            assert step2_resp.status_code == 400

            step3_resp = client.put(
                "/api/v1/projects/demo/drafts/1/step3",
                content="test",
                headers={"content-type": "text/plain"},
            )
            assert step3_resp.status_code == 400
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_files_router.py::TestFilesRouter::test_source_decode_and_draft_mode_helpers -v`
Expected: FAIL — step2 返回 200（目前 step2 是合法的）

- [ ] **Step 3: 实现 — 清理 `_get_step_files()` 和 `_get_step_title()`**

在 `server/routers/files.py` 中修改：

```python
def _get_step_files(content_mode: str) -> dict:
    """根据 content_mode 获取步骤文件名映射"""
    if content_mode == "narration":
        return {1: "step1_segments.md"}
    else:
        return {1: "step1_normalized_script.md"}


def _get_step_title(filename: str) -> str:
    """获取步骤标题"""
    titles = {
        "step1_normalized_script.md": "规范化剧本",
        "step1_segments.md": "片段拆分",
    }
    return titles.get(filename, filename)
```

- [ ] **Step 4: 更新 helper 函数测试**

在 `tests/test_files_router.py` 的 `test_files_helper_functions` 中，替换整个方法体：

```python
    def test_files_helper_functions(self, tmp_path):
        assert files._extract_step_number("step12_x.md") == 12
        assert files._extract_step_number("not-match.md") == 0
        assert files._get_step_files("narration") == {1: "step1_segments.md"}
        assert files._get_step_files("drama") == {1: "step1_normalized_script.md"}
        assert files._get_step_title("step1_segments.md") == "片段拆分"
        assert files._get_step_title("step1_normalized_script.md") == "规范化剧本"
        assert files._get_step_title("unknown.md") == "unknown.md"

        assert files._get_content_mode(tmp_path) == "drama"
        project_json = tmp_path / "project.json"
        project_json.write_text('{"content_mode":"narration"}', encoding="utf-8")
        assert files._get_content_mode(tmp_path) == "narration"
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_files_router.py -v`
Expected: ALL PASS

- [ ] **Step 6: 实现 — 集成事件发射到 `update_draft_content()`**

在 `server/routers/files.py` 顶部添加导入：

```python
from lib.project_change_hints import emit_project_change_batch
```

修改 `update_draft_content()` 函数（432-458行），在 `draft_path.write_text()` 之后添加事件发射：

```python
@router.put("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def update_draft_content(
    project_name: str,
    episode: int,
    step_num: int,
    _user: CurrentUser,
    content: str = Body(..., media_type="text/plain"),
):
    """更新草稿内容"""
    try:
        project_dir = get_project_manager().get_project_path(project_name)
        content_mode = _get_content_mode(project_dir)
        step_files = _get_step_files(content_mode)

        if step_num not in step_files:
            raise HTTPException(status_code=400, detail=f"无效的步骤编号: {step_num}")

        drafts_dir = project_dir / "drafts" / f"episode_{episode}"
        drafts_dir.mkdir(parents=True, exist_ok=True)

        draft_path = drafts_dir / step_files[step_num]
        is_new = not draft_path.exists()
        draft_path.write_text(content, encoding="utf-8")

        # 发射 draft 事件通知前端
        action = "created" if is_new else "updated"
        label_prefix = "片段拆分" if content_mode == "narration" else "规范化剧本"
        change = {
            "entity_type": "draft",
            "action": action,
            "entity_id": f"episode_{episode}_step{step_num}",
            "label": f"第 {episode} 集{label_prefix}",
            "episode": episode,
            "focus": {
                "pane": "episode",
                "episode": episode,
            },
            "important": is_new,
        }
        try:
            emit_project_change_batch(project_name, [change], source="worker")
        except Exception:
            pass  # 事件发射失败不影响主流程

        return {"success": True, "path": str(draft_path.relative_to(project_dir))}

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
```

- [ ] **Step 7: 运行全部测试**

Run: `uv run python -m pytest tests/test_files_router.py tests/test_status_calculator.py -v`
Expected: ALL PASS

- [ ] **Step 8: 提交**

```bash
git add server/routers/files.py tests/test_files_router.py
git commit -m "refactor: 清理 drafts API step2/step3 映射，集成 draft 事件发射"
```

---

### Task 3: 前端类型 — 新增 draft 事件类型

**Files:**
- Modify: `frontend/src/types/workspace.ts`

- [ ] **Step 1: 修改 ProjectChange.entity_type 联合类型**

在 `frontend/src/types/workspace.ts` 中，修改 `ProjectChange` 接口（第10-11行）：

```typescript
export interface ProjectChange {
  entity_type: "project" | "character" | "clue" | "segment" | "episode" | "overview" | "draft";
```

- [ ] **Step 2: 修改 ProjectChangeFocus 接口**

在 `frontend/src/types/workspace.ts` 中，修改 `ProjectChangeFocus` 接口（第3-8行）：

```typescript
export interface ProjectChangeFocus {
  pane: "characters" | "clues" | "episode";
  episode?: number;
  anchor_type?: "character" | "clue" | "segment";
  anchor_id?: string;
  tab?: string;
}
```

- [ ] **Step 3: 构建检查**

Run: `cd frontend && pnpm build`
Expected: 成功（新联合成员仅扩展类型，不破坏现有代码）

- [ ] **Step 4: 提交**

```bash
git add frontend/src/types/workspace.ts
git commit -m "feat: 前端类型新增 draft 事件类型"
```

---

### Task 4: 前端 — project-changes.ts 新增 draft 实体标签

**Files:**
- Modify: `frontend/src/utils/project-changes.ts`

- [ ] **Step 1: 添加 draft 标签**

在 `frontend/src/utils/project-changes.ts` 的 `ENTITY_LABELS` 对象中（第5-12行），添加 `draft` 条目：

```typescript
const ENTITY_LABELS: Record<ProjectChange["entity_type"], string> = {
  project: "项目",
  character: "角色",
  clue: "线索",
  segment: "分镜",
  episode: "剧集",
  overview: "项目概览",
  draft: "预处理",
};
```

- [ ] **Step 2: 构建检查**

Run: `cd frontend && pnpm build`
Expected: 成功

- [ ] **Step 3: 提交**

```bash
git add frontend/src/utils/project-changes.ts
git commit -m "feat: project-changes 新增 draft 实体标签"
```

---

### Task 5: 前端 — useProjectEventsSSE 处理 draft 事件

**Files:**
- Modify: `frontend/src/hooks/useProjectEventsSSE.ts`

- [ ] **Step 1: 添加 draft:created 优先级**

在 `frontend/src/hooks/useProjectEventsSSE.ts` 的 `CHANGE_PRIORITY` 中（第19-29行），在 `"episode:updated"` 之后添加：

```typescript
const CHANGE_PRIORITY: Record<string, number> = {
  "segment:updated": 0,
  "character:created": 1,
  "character:updated": 2,
  "clue:created": 3,
  "clue:updated": 4,
  "episode:created": 5,
  "episode:updated": 6,
  "draft:created": 6.5,
  storyboard_ready: 7,
  video_ready: 8,
};
```

- [ ] **Step 2: 在 onChanges 回调中添加 draft 自动导航**

在 `frontend/src/hooks/useProjectEventsSSE.ts` 的 `onChanges` 回调中（约第246行），在 `groupedChanges` 循环（非 webui 的 toast 推送）之后、现有 `nextFocusTarget` 逻辑之前，插入 draft 导航逻辑：

将第246-263行的代码块替换为：

```typescript
          if (payload.source !== "webui") {
            // Draft 事件 — 自动导航到剧集预处理 Tab
            let draftHandled = false;
            for (const change of payload.changes) {
              if (
                change.entity_type === "draft" &&
                change.action === "created" &&
                typeof change.episode === "number" &&
                !isWorkspaceEditing()
              ) {
                startTransition(() => {
                  setLocation(`/episodes/${change.episode}`);
                });
                draftHandled = true;
                break;
              }
            }

            if (!draftHandled) {
              const nextFocusTarget =
                groupedChanges
                  .map((group) => {
                    const target = getPrimaryGroupTarget(group);
                    if (!target) {
                      return null;
                    }
                    pushWorkspaceNotification({
                      text: formatGroupedDeferredText(group),
                      target,
                    });
                    return target;
                  })
                  .find(Boolean) ?? null;

              queuedFocusRef.current = isWorkspaceEditing() ? null : nextFocusTarget;
            }
          }
```

注意：需要在 hook 函数头部添加 `startTransition` 的使用（已在第1行导入）。

- [ ] **Step 3: 构建检查**

Run: `cd frontend && pnpm build`
Expected: 成功

- [ ] **Step 4: 提交**

```bash
git add frontend/src/hooks/useProjectEventsSSE.ts
git commit -m "feat: useProjectEventsSSE 处理 draft 事件自动导航"
```

---

### Task 6: 前端 — 侧边栏展示 segmented 状态剧集

**Files:**
- Modify: `frontend/src/components/layout/AssetSidebar.tsx:66-71,410-445`

- [ ] **Step 1: 为 segmented 状态添加样式和标签**

在 `frontend/src/components/layout/AssetSidebar.tsx` 中，在剧集列表的 map 回调中（约第413-443行），替换剧集渲染部分：

```tsx
      {episodes.map((ep) => {
        const episodePath = `/episodes/${ep.episode}`;
        const active = isActive(episodePath);
        const isSegmented = ep.script_status === "segmented";
        const statusClass =
          STATUS_DOT_CLASSES[isSegmented ? "draft" : (ep.status ?? "draft")] ??
          STATUS_DOT_CLASSES.draft;

        return (
          <li key={ep.episode}>
            <button
              type="button"
              onClick={() => setLocation(episodePath)}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "bg-gray-800 text-white"
                  : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
              }`}
            >
              <Circle
                className={`h-2.5 w-2.5 shrink-0 fill-current ${statusClass}`}
              />
              <span className="truncate">
                E{ep.episode}: {ep.title}
              </span>
              {isSegmented && !ep.scenes_count && (
                <span className="ml-auto shrink-0 rounded bg-indigo-950 px-1.5 py-0.5 text-[10px] text-indigo-400">
                  预处理
                </span>
              )}
            </button>
          </li>
        );
      })}
```

`isSegmented && !ep.scenes_count` 确保只在「仅有 step1 没有最终剧本」时显示标签。当最终剧本生成后 `scenes_count > 0`，标签消失。

- [ ] **Step 2: 构建检查**

Run: `cd frontend && pnpm build`
Expected: 成功

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/layout/AssetSidebar.tsx
git commit -m "feat: 侧边栏展示仅有预处理的剧集"
```

---

### Task 7: 前端 — PreprocessingView 组件

**Files:**
- Create: `frontend/src/components/canvas/timeline/PreprocessingView.tsx`

- [ ] **Step 1: 创建 PreprocessingView 组件**

```tsx
import { useState, useEffect, useCallback } from "react";
import { Edit3, Save, X } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";

interface PreprocessingViewProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama";
}

export function PreprocessingView({
  projectName,
  episode,
  contentMode,
}: PreprocessingViewProps) {
  const pushToast = useAppStore((s) => s.pushToast);
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setEditing(false);

    API.getDraftContent(projectName, episode, 1)
      .then((text) => {
        if (!cancelled) {
          setContent(text);
          setEditContent(text);
        }
      })
      .catch(() => {
        if (!cancelled) setContent(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [projectName, episode]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await API.saveDraft(projectName, episode, 1, editContent);
      setContent(editContent);
      setEditing(false);
      pushToast("预处理内容已保存", "success");
    } catch {
      pushToast("保存失败", "error");
    } finally {
      setSaving(false);
    }
  }, [projectName, episode, editContent, pushToast]);

  const statusLabel =
    contentMode === "narration" ? "片段拆分已完成" : "规范化剧本已完成";

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-500">
        加载预处理内容...
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-500">
        暂无预处理内容
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Status bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          <span className="text-xs text-gray-500">{statusLabel}</span>
        </div>
        <div className="flex items-center gap-1">
          {editing ? (
            <>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-green-400 transition-colors hover:bg-gray-800 disabled:opacity-50"
              >
                <Save className="h-3.5 w-3.5" />
                {saving ? "保存中..." : "保存"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setEditContent(content);
                }}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800"
              >
                <X className="h-3.5 w-3.5" />
                取消
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
            >
              <Edit3 className="h-3.5 w-3.5" />
              编辑
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      {editing ? (
        <textarea
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          className="min-h-[400px] w-full resize-y rounded-lg border border-gray-700 bg-gray-800 p-4 font-mono text-sm leading-relaxed text-gray-200 outline-none focus:border-indigo-500"
        />
      ) : (
        <div className="prose-invert max-w-none overflow-x-auto rounded-lg border border-gray-800 bg-gray-900/50 p-4 text-sm">
          <StreamMarkdown content={content} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 构建检查**

Run: `cd frontend && pnpm build`
Expected: 成功

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/canvas/timeline/PreprocessingView.tsx
git commit -m "feat: 新增 PreprocessingView 预处理内容查看/编辑组件"
```

---

### Task 8: 前端 — TimelineCanvas Tab 切换 + StudioCanvasRouter 集成

**Files:**
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx`
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.tsx:323-348`

- [ ] **Step 1: 修改 TimelineCanvas — 新增 Tab 切换**

在 `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` 中：

首先添加导入（第1行起）：
```typescript
import { useCallback, useMemo, useRef, useState } from "react";
```

在已有 import 之后添加：
```typescript
import { PreprocessingView } from "./PreprocessingView";
```

修改 Props 接口，新增 `episode` 和 `hasDraft`（第30行起）：
```typescript
interface TimelineCanvasProps {
  projectName: string;
  episode?: number;
  episodeTitle?: string;
  hasDraft?: boolean;
  episodeScript: EpisodeScript | null;
  scriptFile?: string;
  projectData: ProjectData | null;
  onUpdatePrompt?: (segmentId: string, field: string, value: unknown, scriptFile?: string) => void;
  onGenerateStoryboard?: (segmentId: string, scriptFile?: string) => void;
  onGenerateVideo?: (segmentId: string, scriptFile?: string) => void;
  onRestoreStoryboard?: () => Promise<void> | void;
  onRestoreVideo?: () => Promise<void> | void;
}
```

修改组件函数，解构新 props 并添加 Tab 状态（第53行起）：
```typescript
export function TimelineCanvas({
  projectName,
  episode,
  episodeTitle,
  hasDraft,
  episodeScript,
  scriptFile,
  projectData,
  onUpdatePrompt,
  onGenerateStoryboard,
  onGenerateVideo,
  onRestoreStoryboard,
  onRestoreVideo,
}: TimelineCanvasProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentMode = projectData?.content_mode ?? "narration";

  const hasScript = Boolean(episodeScript);
  const showTabs = Boolean(hasDraft);
  const defaultTab = hasScript ? "timeline" : "preprocessing";
  const [activeTab, setActiveTab] = useState<"preprocessing" | "timeline">(defaultTab);
```

修改空状态判断 — 只在两者都没有时才显示空状态（替换第112-118行）：
```typescript
  // Empty state — no episode selected or no content at all
  if (!projectData || (!episodeScript && !hasDraft)) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        请在左侧选择剧集
      </div>
    );
  }
```

修改 `totalDuration` 计算（原第121-123行），添加 null safety：
```typescript
  const totalDuration =
    episodeScript?.duration_seconds ??
    segments.reduce((sum, s) => sum + s.duration_seconds, 0);
```

修改 return 中的 JSX — 在 header 和 segment cards 之间插入 Tab 栏，并根据 activeTab 切换内容。替换第129行起的 return：

```tsx
  return (
    <div ref={scrollRef} className="h-full overflow-y-auto">
      <div className="p-4">
        {/* ---- Episode header ---- */}
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-gray-100">
            {episodeScript
              ? `E${episodeScript.episode}: ${episodeScript.title}`
              : `E${episode ?? "?"}${episodeTitle ? `: ${episodeTitle}` : ""}`}
          </h2>
          {episodeScript && (
            <p className="text-xs text-gray-500">
              {segments.length} {segmentLabel} · 约 {totalDuration}s
            </p>
          )}
        </div>

        {/* ---- Tab bar (only when draft exists) ---- */}
        {showTabs && (
          <div className="mb-4 flex gap-0 border-b border-gray-800">
            <button
              type="button"
              onClick={() => setActiveTab("preprocessing")}
              className={`border-b-2 px-4 py-2 text-sm transition-colors ${
                activeTab === "preprocessing"
                  ? "border-indigo-500 text-indigo-400 font-medium"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              预处理
            </button>
            <button
              type="button"
              onClick={() => hasScript && setActiveTab("timeline")}
              disabled={!hasScript}
              className={`border-b-2 px-4 py-2 text-sm transition-colors ${
                activeTab === "timeline"
                  ? "border-indigo-500 text-indigo-400 font-medium"
                  : !hasScript
                    ? "border-transparent text-gray-700 cursor-not-allowed"
                    : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              剧本时间线
            </button>
          </div>
        )}

        {/* ---- Tab content ---- */}
        {activeTab === "preprocessing" && hasDraft && episode != null ? (
          <PreprocessingView
            projectName={projectName}
            episode={episode}
            contentMode={contentMode}
          />
        ) : episodeScript ? (
          <>
            {/* ---- Segment cards ---- */}
            <div
              className="relative"
              style={{ height: `${virtualizer.getTotalSize()}px` }}
            >
              {virtualItems.map((virtualItem) => {
                const segment = segments[virtualItem.index];
                const segId = getSegmentId(segment, contentMode);
                return (
                  <div
                    id={`segment-${segId}`}
                    key={segId}
                    data-index={virtualItem.index}
                    ref={virtualizer.measureElement}
                    className="absolute left-0 top-0 w-full"
                    style={{
                      transform: `translateY(${virtualItem.start}px)`,
                      paddingBottom: virtualItem.index === segments.length - 1 ? 0 : 16,
                    }}
                  >
                    <SegmentCard
                      segment={segment}
                      contentMode={contentMode}
                      aspectRatio={aspectRatio}
                      characters={projectData.characters}
                      clues={projectData.clues}
                      projectName={projectName}
                      onUpdatePrompt={onUpdatePrompt && ((id, field, value) => onUpdatePrompt(id, field, value, scriptFile))}
                      onGenerateStoryboard={onGenerateStoryboard && ((id) => onGenerateStoryboard(id, scriptFile))}
                      onGenerateVideo={onGenerateVideo && ((id) => onGenerateVideo(id, scriptFile))}
                      onRestoreStoryboard={onRestoreStoryboard}
                      onRestoreVideo={onRestoreVideo}
                    />
                  </div>
                );
              })}
            </div>
          </>
        ) : null}

        {/* Bottom spacer for scroll comfort */}
        <div className="h-16" />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 修改 StudioCanvasRouter — 传递 episode 和 hasDraft**

在 `frontend/src/components/canvas/StudioCanvasRouter.tsx` 中，修改 `/episodes/:episodeId` 路由部分（第323-348行）：

```tsx
      <Route path="/episodes/:episodeId">
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find(
            (e) => e.episode === epNum,
          );
          const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
          const script = scriptFile
            ? (currentScripts[scriptFile] ?? null)
            : null;
          // step1 是 JSON 剧本的前置步骤，有 script_status 非 "none" 说明至少经历过预处理
          // PreprocessingView 内部已处理 draft 不存在的情况（显示"暂无预处理内容"）
          const hasDraft = episode?.script_status === "segmented" || episode?.script_status === "generated";

          return (
            <TimelineCanvas
              projectName={currentProjectName}
              episode={epNum}
              episodeTitle={episode?.title}
              hasDraft={hasDraft}
              episodeScript={script}
              scriptFile={scriptFile ?? undefined}
              projectData={currentProjectData}
              onUpdatePrompt={handleUpdatePrompt}
              onGenerateStoryboard={handleGenerateStoryboard}
              onGenerateVideo={handleGenerateVideo}
              onRestoreStoryboard={handleRestoreAsset}
              onRestoreVideo={handleRestoreAsset}
            />
          );
        }}
      </Route>
```


- [ ] **Step 3: 构建检查**

Run: `cd frontend && pnpm build`
Expected: 成功

- [ ] **Step 4: 运行后端全部测试确认无回归**

Run: `uv run python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/timeline/TimelineCanvas.tsx frontend/src/components/canvas/StudioCanvasRouter.tsx
git commit -m "feat: TimelineCanvas Tab 切换 + StudioCanvasRouter 集成预处理"
```

---

### Task 9: 集成验证

- [ ] **Step 1: 运行后端全量测试**

Run: `uv run python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 2: 运行前端构建 + 类型检查**

Run: `cd frontend && pnpm build`
Expected: 成功

- [ ] **Step 3: 运行前端测试**

Run: `cd frontend && pnpm check`
Expected: 成功

- [ ] **Step 4: Lint + Format**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: 无错误
