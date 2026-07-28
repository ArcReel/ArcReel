# 分镜备注功能实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在分镜卡片文本列下半部分添加备注 textarea，失焦自动保存到剧集 JSON。

**Architecture:** 在 `NarrationSegment` / `DramaScene` 模型中加 `note` 可选字段，复用现有 PATCH API 保存，前端 `TextColumn` 中渲染 textarea。

**Tech Stack:** Python Pydantic, FastAPI, React, TypeScript, Tailwind CSS

---

### Task 1: 后端模型 — 添加 note 字段

**Files:**
- Modify: `lib/script_models.py:86-105`（NarrationSegment）
- Modify: `lib/script_models.py:135-153`（DramaScene）

**Step 1: 在 NarrationSegment 中添加 note 字段**

在 `generated_assets` 字段之前添加：

```python
note: Optional[str] = Field(default=None, description="用户备注（不参与生成）")
```

**Step 2: 在 DramaScene 中添加 note 字段**

同样在 `generated_assets` 字段之前添加：

```python
note: Optional[str] = Field(default=None, description="用户备注（不参与生成）")
```

**Step 3: 运行测试验证无破坏**

Run: `python -m pytest tests/ -x -q`
Expected: 全部通过（Optional + default=None 兼容旧数据）

**Step 4: Commit**

```bash
git add lib/script_models.py
git commit -m "feat(model): add note field to NarrationSegment and DramaScene"
```

---

### Task 2: 后端 API — 允许 note 字段更新

**Files:**
- Modify: `server/routers/projects.py:397-398`（update_scene 允许列表）
- Modify: `server/routers/projects.py:419-425`（UpdateSegmentRequest）
- Modify: `server/routers/projects.py:452-461`（update_segment handler）

**Step 1: Drama 模式 — update_scene 允许列表加入 note**

在 `server/routers/projects.py:397` 的允许字段列表中加入 `"note"`：

```python
if key in ["duration_seconds", "image_prompt", "video_prompt",
           "characters_in_scene", "clues_in_scene", "segment_break", "note"]:
```

**Step 2: Narration 模式 — UpdateSegmentRequest 加入 note 字段**

在 `server/routers/projects.py:425` 的 `transition_to_next` 之后添加：

```python
note: Optional[str] = None
```

**Step 3: Narration 模式 — update_segment handler 处理 note**

在 `server/routers/projects.py:461`（`transition_to_next` 处理之后）添加：

```python
if req.note is not None:
    segment["note"] = req.note
```

**Step 4: 运行测试验证**

Run: `python -m pytest tests/ -x -q`
Expected: 全部通过

**Step 5: Commit**

```bash
git add server/routers/projects.py
git commit -m "feat(api): allow note field in segment/scene PATCH endpoints"
```

---

### Task 3: 前端类型 — 添加 note 字段

**Files:**
- Modify: `frontend/src/types/script.ts:69-81`（NarrationSegment）
- Modify: `frontend/src/types/script.ts:83-94`（DramaScene）

**Step 1: 在 NarrationSegment interface 添加 note**

在 `generated_assets` 之前添加：

```typescript
note?: string;
```

**Step 2: 在 DramaScene interface 添加 note**

同样在 `generated_assets` 之前添加：

```typescript
note?: string;
```

**Step 3: 运行类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: 通过

**Step 4: Commit**

```bash
git add frontend/src/types/script.ts
git commit -m "feat(types): add note field to NarrationSegment and DramaScene"
```

---

### Task 4: 前端 UI — TextColumn 中渲染备注区

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:190-237`（TextColumn）
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:545-621`（SegmentCard 主组件传参）

**Step 1: 修改 TextColumn 组件**

给 TextColumn 增加 `onUpdateNote` 回调 prop，在原文/对话下方渲染备注 textarea：

```tsx
function TextColumn({
  segment,
  contentMode,
  onUpdateNote,
}: {
  segment: Segment;
  contentMode: "narration" | "drama";
  onUpdateNote?: (value: string) => void;
}) {
  const [noteDraft, setNoteDraft] = useState(segment.note ?? "");
  const committedRef = useRef(segment.note ?? "");

  // 当 segment 数据从外部更新时同步
  useEffect(() => {
    setNoteDraft(segment.note ?? "");
    committedRef.current = segment.note ?? "";
  }, [segment.note]);

  const handleNoteBlur = () => {
    if (noteDraft !== committedRef.current) {
      committedRef.current = noteDraft;
      onUpdateNote?.(noteDraft);
    }
  };

  // ... 现有的 narration/drama 渲染逻辑保持不变 ...
  // 在 return 的 div 末尾追加备注区：

  return (
    <div className="flex flex-col gap-1.5 p-3">
      {/* 现有原文/对话内容 */}
      ...

      {/* 备注区 */}
      <div className="mt-auto pt-3 border-t border-gray-800">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 block">
          备注
        </span>
        <textarea
          className="w-full resize-none rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-sm text-gray-300 placeholder-gray-600 focus:border-indigo-500 focus:outline-none"
          rows={4}
          placeholder="添加备注..."
          value={noteDraft}
          onChange={(e) => setNoteDraft(e.target.value)}
          onBlur={handleNoteBlur}
        />
      </div>
    </div>
  );
}
```

**Step 2: SegmentCard 传递 onUpdateNote 到 TextColumn**

在 SegmentCard 的 TextColumn 渲染处添加回调：

```tsx
<TextColumn
  segment={segment}
  contentMode={contentMode}
  onUpdateNote={(value) => onUpdatePrompt?.(segmentId, "note", value)}
/>
```

**Step 3: 运行类型检查和前端测试**

Run: `cd frontend && pnpm check`
Expected: typecheck 和 test 均通过

**Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx
git commit -m "feat(ui): add note textarea to segment card TextColumn"
```

---

### Task 5: 端到端验证

**Step 1: 启动后端**

Run: `uv run uvicorn server.app:app --reload --port 1241`

**Step 2: 启动前端**

Run: `cd frontend && pnpm dev`

**Step 3: 手动验证**

1. 打开浏览器，进入一个项目的分镜页面
2. 在任意分镜卡片的文本列下方看到 "备注" 标签和 textarea
3. 输入备注内容，点击其他地方（触发 blur）
4. 刷新页面，确认备注内容已保存
5. 检查 JSON 文件确认 `note` 字段已写入

**Step 4: 运行全部测试**

Run: `python -m pytest tests/ -x -q && cd frontend && pnpm check`
Expected: 全部通过
