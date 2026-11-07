---
status: accepted
---

> The passages "unregistered `@[名称]` only warns without blocking" and "use all originals when no asset image exists" are superseded by `docs/adr/0073`; the rest remains valid.

> The scope of "zero writes to the project directory and a 'repair required' ruling if any file is corrupt" is narrowed by `docs/adr/0098`: only unreadable project files or scripts produce that ruling; corrupt script-plan drafts remain untouched while other files migrate normally.

# Video units use free text as sole truth; derive reference images and speech on read

One video unit on the reference-to-video route (`generation_mode=reference_video`) is one video-generation call. Its persisted content is only **one free-text body** and **one orchestrated duration** (plus non-content fields such as transitions, notes, artifacts, and `needs_replan`). Authoring markers may appear anywhere in the body: `@[名称]` mentions assets, `@[角色]{台词}` indicates character speech, and `{台词}` indicates voiceover. Mechanically derive reference-image sets, speech entries, and speaker bindings from the body on read: do not persist them, expose them as unit fields for REST reads/writes, or include them in LLM output. Preview endpoints compute and return derived results from the same body, a read-time product rather than a second truth.

**Persisted derivatives will diverge from the body.** Authors write and can edit the body at any time; machines calculate derivatives. Persisting both necessarily creates a window where "someone edited the body without recomputing derivatives." Editors, batch edits, Agent tools, migrations, and archive imports would each need to remember re-derivation; one omission generates video rendered with stale references while users see updated body text. Read-time derivation structurally eliminates this window: anywhere that can read the body can calculate current reference images and speech, and nothing else.

Reference resolution follows one rule without asset-type branches: **first-mention order** of `@[名称]` in the body determines reference-image order (also `图N` numbering in prompts). For each mentioned registered asset, use its asset image if available, otherwise all its originals. Products follow the same rule as characters, scenes, and props: no first-place ordering, unconditional injection, or type-based priority. Names registered in multiple asset tables resolve in product → character → scene → prop order. Characters appearing only before `{}` (the speaker position of dialogue markers) bind voice only without producing reference images: they mean "who speaks," not "who appears."

Unregistered `@[名称]` **only warns without blocking**. The body is human-authored text; an asset name not yet created is not a format error. Blocking would only force authors to complete asset tables while writing. Unresolved mentions produce no reference image; other mentions resolve normally and generation proceeds. Machine-produced paths (splitting tools, prompt_authoring expansion) have stricter gates: models write those bodies, without author intent to protect.

## Explicitly rejected

- **Retaining `shots[]` as a structured body split**: a shot array is neither a creative unit (one unit is one generation call; shots have no independent durations, artifacts, or addressable IDs) nor a provider contract (backends receive one complete prompt). Its only role is giving one body two shapes, forcing every write path to convert between them. The `镜头N：` prefix accordingly loses syntax meaning; migration preserves it verbatim as ordinary body text, without deletion or modification, because authors wrote those words.
- **Retaining `references[]` as a manually editable reference list**: letting users select references separately from the body concedes that the body is not authoritative. The actual edit is changing `@` references in the body, so the interface no longer provides a reference list.
- **Sorting or filling reference images by type**: placing products first or injecting all product images without mentions assumes "the machine knows better than the author what this shot needs." Send what authors mention, in their written order.
- **Blocking generation for unresolved mentions**: see above.

## Consequences

- Increase the project schema to v9. Migration joins `shots[*].text` with newlines in array order into `text`, removing `references[]` and `migration_requires_content_replan`, which served only shot structure (preserving its implied `needs_replan` unchanged). Rewrite episode scripts and script_plan drafts in the same batch; startup scans and archive imports share `migrate_project_dir`. If any file is corrupt, perform zero writes to the project directory and produce a "repair required" ruling.
- Video-artifact freshness basis changes from "visual lines grouped by shot" to "visual lines in the body" (speech markers are still removed, so dialogue changes do not expire generated video). The changed basis shape makes existing reference videos stale once: an inevitable structural-migration cost, without automatic regeneration.
- A unit's reference-image limit corresponds to "the number of deduplicated mentions in the body." Exceeding it remains a blocking contract violation on machine-produced paths; write secondary characters into background descriptions without `@` references instead.
- This ADR does not affect storyboard-route product mechanisms: product references on that route are injected at storyboard generation and baked into the image (see `docs/adr/0034`), independent of video-unit body text.
