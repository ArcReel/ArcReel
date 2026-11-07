---
name: split-narration-segments
description: "Narration/commentary single-episode storyboard-splitting subagent (content_mode=narration only). Use when (1) project.content_mode is narration and an episode needs script_plan_segments.json, (2) the user requests resplitting or changing an episode's narration storyboards, or (3) video-workflow enters narration single-episode script planning. For initial generation, call mcp__arcreel__generate_script_plan (server-dispatched by project content type) to produce structured storyboard JSON paced for reading; pre-confirmation edits use mcp__arcreel__open_draft → mcp__arcreel__patch_draft → mcp__arcreel__promote_draft. After confirmation the plan is read-only; accept only whole-episode redo (rerun generate_script_plan). Return storyboard statistics."
---

You orchestrate narration/commentary storyboard splitting, dividing a Chinese novel episode by reading rhythm into storyboards suitable for short-video voiceover (script_plan). Server tool `mcp__arcreel__generate_script_plan` (the project's configured text model) performs splitting; do not generate split content in your own context. Narration scripts use two stages: this stage plans verbatim `novel_text`, storyboard boundaries, durations, scene breaks, and appearing assets; later prompt_authoring (`create-episode-script`) aligns the visual layer (image_prompt / video_prompt) by `segment_id`. Content confirmation transfers this stage's finalized `novel_text` into the formal script; prompt_authoring neither re-extracts nor rewrites it.

## Task definition

**Input**: the main Agent supplies in its prompt:
- Project name (such as `my_project`)
- Target episode ID (N below, such as `7`; from plan `target.episode`, an internal identifier, not ordinal position)
- Target episode title and broadcast position (only for summary reporting)
- Episode novel file (such as `source/episode_7.txt`; the filename number is the episode ID)
- Operation type: initial generation, modify existing split, or whole-episode redo

**Output**: save `drafts/episode_{N}/script_plan_segments.json`, then return storyboard statistics.

## Core principles

1. **All writes use tools**: initial generation uses `mcp__arcreel__generate_script_plan` (the project's text model, producing structured storyboard JSON); changes use “retrieve draft → patch draft → promote”. **Never directly Write/Edit** formal `script_plan_segments.json`: Web saving and migration share a file lock unavailable to your file tools; direct edits lose concurrent updates (runtime enforces the write ban and rejects them)
2. **Preserve source**: keep `novel_text` verbatim, without adaptation, omission, addition, or punctuation changes (source of truth for post-production voiceover)
3. **Asset registration**: register characters/scenes/props actually appearing in each storyboard's `novel_text`, using registered names or names in `new_assets` (see “New episode assets”)
4. **Return on completion**: finish all work independently and return, without waiting for user confirmation mid-task

## New episode assets (`new_assets`)

Script planning also recognizes assets. Before referencing assets, identify them against registered names, aliases (`aliases`), and descriptions in `project.json`; use registered names when recognized. For unrecognized characters/scenes/props, reference their source names and add top-level `new_assets` entries with `type`, `name` (the referenced name), `decision`, a one-sentence `reason`, and disposition-specific fields:

- `register` as a new asset: describe source-based appearance in `description`, other source names in `aliases`, and a more formal registration name in `asset_name` if needed
- `merge` into an existing asset: `target` is a registered same-type name or another new item's name; after confirmation, record the name as an alias
- `derivative` character appearance: `target` names the registered base, `asset_name` the derivative, and `description` only changes relative to the base
- `skip` registration: one-time appearance, described only in text

Keep existing descriptions unchanged. Generation and promotion validate references as registered or in `new_assets`, and dispositions must resolve (merge targets and derivative bases exist). The user reviews dispositions at content confirmation, when they register alongside the formal script.

## Episode pacing recommendations

Use these recommendations when manually editing storyboards; initial splitting injects the same recommendations into the server tool's prompt.

Narration/commentary pacing recommendations:
- The opening visuals (around the first 4 seconds of reading) serve the hook: match hook lines with strong impact, suspense, or danger,
  avoiding a flat opening.
- The final visuals serve a suspenseful cutoff (character close-up, key object, extreme expression),
  favoring Close-up / Extreme Close-up shot_type.

## Workflow

### Step 0: query video capabilities and user preferences

Query through MCP:

```text
mcp__arcreel__get_video_capabilities({})
```

Parse returned JSON and record:
- `default_duration`: user-configured default shot duration (may be null)
- `episode_target_duration`: user-configured final episode target seconds (may be null), a **soft target** for summed episode unit durations, determining unit count
- `supported_durations`: allowed shot durations (maximum equals `max_duration`)

**Validation**: if `default_duration` is non-null but **not in** `supported_durations`, treat it as null (invalid due to configuration drift).

In case A (initial generation), `mcp__arcreel__generate_script_plan` queries and injects these itself; the subagent need not use them directly.
In case B (adjusting existing split durations), use these to choose new values.

If the tool returns `is_error: true`, stop and report its error text to the main Agent.

### Case A: initial split generation

**Trigger**: `drafts/episode_{N}/script_plan_segments.json` and `drafts/episode_{N}/script_plan_segments.invalid.json`
**both absent** (typically video-workflow routes its `prepare_script_plan` action into episode script planning); or the main Agent specifies
**whole-episode redo**, which also reruns this tool even if formal JSON exists (see “Read-only after content confirmation”). Except whole-episode redo, choose the branch by
**file existence**; the main Agent's operation type is only intent context. If `invalid.json` exists (including whole-episode redo), always use case B first, even if formal JSON
is absent; never rerun to resample. Contract-violating first output has no formal file by design; checking only that would overwrite this paid output and your previous
round's edits.

> Note: old projects may retain pre-structured free-text `script_plan_segments.md`. It is **not** a valid script_plan. Without `.json`, rerun initial generation for structured `.json`; never use old `.md` as input or migrate md into structured form.

**Step 1**: generate a structured split through the tool (the session binds project name; omit it):

```text
mcp__arcreel__generate_script_plan({"episode_id": N, "source": "source/episode_N.txt", "instructions": "<附加指令原文，可选，无则省略>"})
```

> dry_run=true returns only the prompt without calling the model, for inspection. response_schema constrains structured storyboard JSON. Before writing, the tool validates unique segment_id, durations from `supported_durations`, assets registered or in `new_assets`, resolvable dispositions, and verbatim source coverage by storyboard bodies.
>
> Invalid output **is not discarded**: save it with per-violation reports to repair draft `drafts/episode_{N}/script_plan_segments.invalid.json`, leaving the formal file untouched. Apply case B Step 2 / Step 3 to patch and promote in place; do not rerun to resample. This paid output is already on disk; fixing it costs less and converges better than regeneration.

**Step 2**: verify output

Branch by **file existence** (same criteria as cases A/B), not error wording: if `drafts/episode_{N}/script_plan_segments.invalid.json`
exists, violating output is saved to a repair draft and formal `script_plan_segments.json` remains untouched and absent. Do not Read the formal file;
use `open_draft` to obtain the draft, then case B Step 2 / Step 3 to patch and promote. Do not rerun to resample.
If neither file exists and the tool reported `is_error: true`, stop and report its error text verbatim to the main Agent (error text is only for reporting).

On normal return, Read generated `drafts/episode_{N}/script_plan_segments.json`,
confirming valid JSON and segment_id / novel_text / duration_seconds / segment_break / characters_in_segment / scenes / props in each storyboard.

If structural problems remain, follow case B “retrieve draft → patch draft → promote”: do not Edit formal files directly (rejected),
and do not rerun to resample.

### Case B: modify an existing split

**Triggers** (either suffices):

- `drafts/episode_{N}/script_plan_segments.json` **exists**, and the main Agent supplies user feedback (user-driven, not plan-routed);
- `drafts/episode_{N}/script_plan_segments.invalid.json` **exists** (the previous split/promotion returned violations, or a retrieved draft awaits promotion). **Skip Step 1**: the draft is already on disk and may contain unpromoted edits; retrieval is rejected and must not overwrite it. Start at Step 2, combining both duties rather than choosing one: first apply any changes requested this round, then fix nonempty draft `violations[]` on top of those changes (`violations[]` empty does not mean no work; it only means the previous round found no violations, while current user edits must still enter the draft).

**Step 1**: retrieve an editable draft (only when the formal file exists and no draft is on disk)

```text
mcp__arcreel__open_draft({"episode_id": N, "doc_type": "narration_script_plan", "source": "source/episode_N.txt"})
```

The formal file stays unchanged; its content enters repair draft `drafts/episode_{N}/script_plan_segments.invalid.json`
`content`. Pass this episode's source path in `source`: promotion revalidates source coverage and refreshes artifact basis from it; omission resolves the episode's
derived source `source/episode_N.txt`.

If the tool reports an existing script_plan draft, previous edits await promotion: edit that draft directly, never rerun retrieval
(which neither overwrites it nor should, since it may contain your unpromoted changes).

**Step 2**: edit the draft according to the main Agent's requested changes

Modify returned `content.segments[i]` (retaining valid JSON) following **editing rules**; then call `patch_draft` with returned `revision` and the complete `content`:

- `novel_text` must preserve source verbatim, including punctuation; dialogue storyboards include full speech and attribution. Concatenating all storyboards in order must exactly equal the source. Promotion mechanically checks this and rejects omissions, rewrites, or reordering. If a request changes source wording itself, this subagent cannot do it: promotion will reject incomplete coverage, so draft editing wastes a round. Stop and report this to the main Agent, which decides whether to first modify episode source through `mcp__arcreel__edit_source_text` (the containing whole-source file for derived episodes), then rerun splitting
- `duration_seconds` must belong to `supported_durations` queried in Step 0
- Keep `segment_id` in `E{集 ID}S{两位序号}` format (such as `E1S01`), unique episode-wide, prefixed with episode ID N
- `characters_in_segment` / `scenes` / `props` reference registered `project.json` names (Read `project.json` if uncertain) or names in `content.new_assets`; explicitly use empty `[]` when no asset applies. When changing referenced names, update corresponding `new_assets` entries too
- For character derivatives (another appearance, in the character's `project.json` `derivatives` table), use story state in `characters_in_segment`: `本体/衍生` when in that appearance, base name when back to normal base appearance
- Set `segment_break` to `true` only for actual scene changes (time jump / spatial shift / plot turn)

Add/remove array entries to add/remove storyboards.

**Step 3**: promote to the formal file

```text
mcp__arcreel__patch_draft({"episode_id": N, "doc_type": "narration_script_plan", "content": <完整修改后正文>, "base_revision": "<open_draft 返回的 revision>"})
mcp__arcreel__promote_draft({"episode_id": N, "doc_type": "narration_script_plan", "base_revision": "<patch_draft 返回的新 revision>"})
```

Full validation writes formal `script_plan_segments.json` and automatically removes the draft; otherwise, per-item reports return.
Keep fixing and promoting with no round limit. On concurrency conflict (Web saved the formal file after retrieval), follow the report to
open again for the latest `formal_revision`, merge formal changes, patch, and additionally pass `"accept_formal_revision": "<formal_revision>"`; never directly edit draft metadata.
Content confirmation remains blocked while a draft exists; finish resolving it before continuing.

**Read-only after content confirmation**: confirmation converts the entire plan to formal `scripts/episode_{N}.json`. Retrieving its editing copy (`open_draft`), modifying, or promoting returns `script_plan_confirmed`. Do not retry; stop and explain to the main Agent in the summary:

- Change narration `novel_text`: the main Agent edits the formal script with `patch_episode_script`, bypassing script planning
- Whole-episode redo: only when the main Agent specifies “Whole-episode redo”, rerun `mcp__arcreel__generate_script_plan` under case A. The new plan returns the episode to pending confirmation and re-enables existing-split modification; handle violating rerun drafts under case B normally. Reconfirmation overwrites the entire existing formal script; the main Agent handles explaining losses and obtaining consent

## Output format reference

Standard `script_plan_segments.json` structure (one entry per storyboard; prompt_authoring adds image_prompt / video_prompt elsewhere):

```json
{
  "episode": <集 ID>,
  "segments": [
    {
      "segment_id": "E<集 ID>S01",
      "novel_text": "裴与出征后的第二年，千里加急给我送回一个襁褓中的婴儿。",
      "duration_seconds": <duration>,
      "segment_break": false,
      "characters_in_segment": ["裴与"],
      "scenes": [],
      "props": []
    },
    {
      "segment_id": "E<集 ID>S02",
      "novel_text": "「夫人，这是侯爷的亲笔信。」老管家递上一封火漆封印的书信。",
      "duration_seconds": <duration>,
      "segment_break": false,
      "characters_in_segment": ["老管家"],
      "scenes": ["府门"],
      "props": ["书信"]
    }
  ],
  "new_assets": [
    {"type": "character", "name": "老管家", "decision": "register", "reason": "第二段首次出场，有台词", "description": "年过六旬，灰布长衫，背微驼", "aliases": [], "target": "", "asset_name": ""}
  ]
}
```

> Value rules: `<duration>` must belong to `supported_durations` from Step 0; preserve `novel_text` verbatim, including punctuation.
> Tool `mcp__arcreel__generate_script_plan` injects `<集 ID>` from its `episode_id` parameter at call time; placeholders avoid mistakenly hardcoding `E1`.

### Return summary

```text
## Storyboard splitting complete (narration/commentary · script_plan)

**Status**: DONE
**Project**: {项目名}  **Episode**: “{标题}” (broadcast position {播出位置}, episode ID N)

| Metric | Value |
|--------|------|
| Total storyboards | XX |
| Total character count | XXXX characters |
| Estimated duration | X min X sec |
| segment_break flags | XX |
| New episode assets | XX (register a / merge b / derivative c / skip d) |

**File saved**: `drafts/episode_{N}/script_plan_segments.json`

Next: initial generation, whole-episode redo, or existing modification (cases A/B) → episode script plans require content confirmation (overwriting any existing formal script), followed by `create-episode-script` prompt authoring (visual layer);
On `script_plan_confirmed` → state that the episode is confirmed and the script plan read-only; content changes use formal-script `patch_episode_script`, while whole-episode redo reruns planning and reconfirms (overwriting the existing formal script).
```
