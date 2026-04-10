# Storyboard Note Feature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a note textarea to the lower portion of the storyboard card text column, auto-saving to the episode JSON on blur.

**Architecture:** Add an optional `note` field to the `NarrationSegment` / `DramaScene` models, reuse the existing PATCH API for saving, and render a textarea in the frontend `TextColumn`.

**Tech Stack:** Python Pydantic, FastAPI, React, TypeScript, Tailwind CSS

---

### Task 1: Backend model — add note field

**Files:**
- Modify: `lib/script_models.py:86-105` (NarrationSegment)
- Modify: `lib/script_models.py:135-153` (DramaScene)

**Step 1: Add note field to NarrationSegment**

Add before the `generated_assets` field:

```python
note: Optional[str] = Field(default=None, description="User note (not used in generation)")
```

**Step 2: Add note field to DramaScene**

Similarly, add before the `generated_assets` field:

```python
note: Optional[str] = Field(default=None, description="User note (not used in generation)")
```

**Step 3: Run tests to verify no breakage**

Run: `python -m pytest tests/ -x -q`
Expected: All pass (Optional + default=None is compatible with old data)

**Step 4: Commit**

```bash
git add lib/script_models.py
git commit -m "feat(model): add note field to NarrationSegment and DramaScene"
```

---

### Task 2: Backend API — allow note field updates

**Files:**
- Modify: `server/routers/projects.py:397-398` (update_scene allowed list)
- Modify: `server/routers/projects.py:419-425` (UpdateSegmentRequest)
- Modify: `server/routers/projects.py:452-461` (update_segment handler)

**Step 1: Drama mode — add note to update_scene allowed list**

Add `"note"` to the allowed field list at `server/routers/projects.py:397`:

```python
if key in ["duration_seconds", "image_prompt", "video_prompt",
           "characters_in_scene", "clues_in_scene", "segment_break", "note"]:
```

**Step 2: Narration mode — add note field to UpdateSegmentRequest**

Add after `transition_to_next` at `server/routers/projects.py:425`:

```python
note: Optional[str] = None
```

**Step 3: Narration mode — handle note in update_segment handler**

Add after the `transition_to_next` handling at `server/routers/projects.py:461`:

```python
if req.note is not None:
    segment["note"] = req.note
```

**Step 4: Run tests to verify**

Run: `python -m pytest tests/ -x -q`
Expected: All pass

**Step 5: Commit**

```bash
git add server/routers/projects.py
git commit -m "feat(api): allow note field in segment/scene PATCH endpoints"
```

---

### Task 3: Frontend types — add note field

**Files:**
- Modify: `frontend/src/types/script.ts:69-81` (NarrationSegment)
- Modify: `frontend/src/types/script.ts:83-94` (DramaScene)

**Step 1: Add note to NarrationSegment interface**

Add before `generated_assets`:

```typescript
note?: string;
```

**Step 2: Add note to DramaScene interface**

Similarly, add before `generated_assets`:

```typescript
note?: string;
```

**Step 3: Run type check**

Run: `cd frontend && pnpm typecheck`
Expected: Pass

**Step 4: Commit**

```bash
git add frontend/src/types/script.ts
git commit -m "feat(types): add note field to NarrationSegment and DramaScene"
```

---

### Task 4: Frontend UI — render note area in TextColumn

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:190-237` (TextColumn)
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:545-621` (SegmentCard main component props)

**Step 1: Modify TextColumn component**

Add `onUpdateNote` callback prop to TextColumn, rendering a note textarea below the original text/dialogue:

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

  // Sync when segment data is updated from outside
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

  // ... existing narration/drama rendering logic remains unchanged ...
  // Append the note area at the end of the return div:

  return (
    <div className="flex flex-col gap-1.5 p-3">
      {/* Existing source text/dialogue content */}
      ...

      {/* Note area */}
      <div className="mt-auto pt-3 border-t border-gray-800">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 block">
          Note
        </span>
        <textarea
          className="w-full resize-none rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-sm text-gray-300 placeholder-gray-600 focus:border-indigo-500 focus:outline-none"
          rows={4}
          placeholder="Add a note..."
          value={noteDraft}
          onChange={(e) => setNoteDraft(e.target.value)}
          onBlur={handleNoteBlur}
        />
      </div>
    </div>
  );
}
```

**Step 2: Pass onUpdateNote from SegmentCard to TextColumn**

Add the callback where TextColumn is rendered in SegmentCard:

```tsx
<TextColumn
  segment={segment}
  contentMode={contentMode}
  onUpdateNote={(value) => onUpdatePrompt?.(segmentId, "note", value)}
/>
```

**Step 3: Run type check and frontend tests**

Run: `cd frontend && pnpm check`
Expected: Both typecheck and tests pass

**Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx
git commit -m "feat(ui): add note textarea to segment card TextColumn"
```

---

### Task 5: End-to-end verification

**Step 1: Start backend**

Run: `uv run uvicorn server.app:app --reload --port 1241`

**Step 2: Start frontend**

Run: `cd frontend && pnpm dev`

**Step 3: Manual verification**

1. Open the browser, navigate to a project storyboard page
2. See the "Note" label and textarea below the text column of any storyboard card
3. Enter note content, click elsewhere (triggering blur)
4. Refresh the page and confirm the note content has been saved
5. Check the JSON file to confirm the `note` field was written

**Step 4: Run all tests**

Run: `python -m pytest tests/ -x -q && cd frontend && pnpm check`
Expected: All pass
