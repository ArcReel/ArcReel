---
status: accepted
---

# One complete template per generation stage; resolve axis differences through named partial variants, bundled in the core library

Approximately 60 prompt definitions span seven carriers (Python constants, large f-strings, line concatenation, dict registries, Markdown, project fields, runtime system prompts). Four paths inject one project style field with four phrasings; five separately phrase negatives. Creators cannot see complete stage prompts, and maintainers must locate constants/copies before changing wording.

**Exactly one template file per generation stage, containing the complete call prompt with data slots only**; extract shared wording into partials. **Resolve axis-specific wording (source kind, asset type, ad-duration tier, single-line content-mode differences) through named partial variants, never comparing axes inside templates**. Conditions test only slot truthiness for optional data blocks (ending hooks, target durations, extra instructions, next outlines). **Bundle templates inside core-library packages, outside Agent runtime profiles**. **Inject template directories as module construction dependencies**, without self-location.

Three reasons determine these choices:

- **Simpler existing mechanisms.** One file answers what each stage sends; duplicated wording becomes partial references, so wording changes edit one file.
- **User-visible prompts instead of black boxes.** Settings list all built-ins by category and show sources. Less syntax improves readability; axis conditionals require execution or mental branch tracing, while separate files let settings enumerate wording per axis value without execution.
- **Prepare for user editing/sharing.** Future users edit prose templates/partials without ArcReel code. Editing axis-specific wording affects one isolated file, not other values; adding source/asset types adds files instead of editing templates.

**Bundle in core packages** because templates are code, not Agent-visible instructions: Docker/read-only installations need no copying/environment variables, and templates never materialize into projects. Runtime-profile Markdown (CLAUDE.md, skills, subagents, references) loads verbatim through SDK without rendering and stays outside this mechanism; pretending it is templates mixes semantics. **Inject directories** so tests target fixtures for errors and built-ins for completeness, while production targets bundled files.

## Four boundaries

These are prerequisites; tests guard automatically checkable parts, while the first relies on ADR/review.

- **Truthiness slots must not derive from axis comparisons.** Booleans represent only project-data presence. Code must not compare `source_kind` / `content_mode` / `asset_type` / `generation_mode` to create boolean slots (existing `is_screenplay`, likewise `inject_style`, are counterexamples). Otherwise differences merely move into slot names, losing settings enumerability. **Directory scans cannot check this; review must.**
- **Variants carry wording differences; code carries value differences.** `aspect_ratio_label` (9:16 → portrait composition / 16:9 → landscape / other ratios in sentences) is a computed short value label, not an axis, supplied by code without variant families. Ask whether branches change values or wording: wording belongs in partials, value rules in code.
- **Slot values are complete-key projections.** Producers complete nested shapes (`project_overview.synopsis`, `episode_outline.hook`), using null for absent values; templates never use `.get()`. StrictUndefined missing-key failures are intentional: wording edits must never silently drop project data.
- **Partial names must be literals; loops occur only in list partials.** Names are paths relative to `partials/`, without implicit lookup; concatenated names invalidate static loading scans, leaving only runtime fallback. Loops expand list slots (candidate names, outline beats, asset appearances), only in list partials, never main bodies.

Validate variant completeness at load using frontmatter `applies_to`, failing loudly for missing axis-value files. **Intentional silence uses empty files**, never missing-file-means-empty, which conflates intent with omission.

## Explicitly rejected

- **Axis conditionals in templates** (`{% if source_kind == "screenplay" %}`): settings must execute to reveal wording; users locate branches to edit one value, with changes spanning all values. All three reasons reject this.
- **Template directories inside Agent profiles**: profiles hold verbatim instructions, templates server-rendered data. Combining makes materialization, sha256 three-state sync, and reset manage files never meant for materialization, tying core rendering to profile deployment.
- **`{% include %}` partials**: `trim_blocks` consumes post-tag newlines, making line joining unpredictable. Use expressions (`partial(...)` / `variant(...)`), strip trailing partial newlines, and let reference sites own newlines, covering inline/full-line usage uniformly.
- **Self-located directories**: invalid-path tests would need monkeypatches/environment variables, while directory location is deployment configuration.
- **Prerendering every list into string slots in code**: hides wording such as "Story beats:", "(None yet)", and "Current form:" back in code, contradicting visible prompt text.

## Consequences

- One module is the repository's prompt-text rendering outlet, exposing only render by ID, list metadata, and read source/partial lists by ID. New stages add template files, not builder functions.
- New axis values (source/asset types, duration tiers) require a file in every variant family on that axis, including intentionally empty ones. Omissions fail at load, not generation.
- Built-in-directory tests enforce checkable rules: no axis comparisons, complete variants, literal names, loops only in list partials, deduplication only for plain-text round-trip templates. No automation protects against axis-derived boolean slots; reviewers inspect their origins.
- Extract shared partials only when used by at least two generation stages, counting all consolidated stages rather than only migrated ones; single-template wording stays inline. The same threshold applies between partials: single-parent wording stays inside its parent, without inherited sharing claims. Shared edits intentionally affect multiple stages, requiring per-stage semantic checks during migration.
- Template wording changes do not stale artifacts: templates supply derived rendering wrappers and stay outside bases under `docs/adr/0070`.
- Main-body reference sites own block spacing. Empty/deduplicated block references (expressions alone on lines) remove their whole lines including trailing newlines, avoiding blank lines in consecutive empty variants. After whole rendering, collapse only template-created excess blank lines; preserve slot/user-data blanks and leading/trailing newlines verbatim.
- Idempotent deduplication is opt-in through frontmatter `idempotent: true`, only for plain-text round-trip templates (`storyboard/image`, `storyboard/video`), never generic. Other templates never skip partials: repeated data such as asset-list "(None yet)" or shared current/next outline partials must not disappear. Opted-in deduplication covers only block references and works per line: skip complete lines already in bodies/prior partials, inject the rest. Style `Style` and `Visual style` lines are independent, so changed values on one do not repeat another. Always inject inline references, avoiding accidental short-string matches; data-bearing `lists/` partials never deduplicate in any template. `docs/adr/0070`'s repeat-rendered-plain-text rule consequently requires these two templates to opt in and use block references for shared style/`Avoid` lines.
- Frontmatter `description` is one sentence on template purpose for settings users. Wording rationale (such as "on-screen people" rather than "people" to preserve portraits/package illustrations) belongs in commits/this ADR, never `description` or Jinja comments: partial bodies are prompts, and Jinja comments appear in settings alongside source.
- Do not build/reserve template-language variants (`applies_to` has no language axis); model-facing template/partial bodies retain repository i18n exemption.
