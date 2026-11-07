---
status: accepted
---

# Migration backfills typed provenance for legacy media versions from project state at migration time, then treats them like generation-frozen records

> This ADR's "register scripts without formal script_plan using no-plan basis; replanning makes scripts stale because the basis changes" is superseded by `docs/adr/0080`: script basis does not use script plans as input and registers the same basis with or without plans. Legacy-media provenance projection remains valid.
>
> This ADR's "report projection failures (missing storyboard images, entries ineligible for speech synthesis, unresolvable duration tiers, etc.) in migration reports for users to decide regeneration" is superseded by `docs/adr/0098`: these gaps no longer prevent projection, and videos are backfilled normally. The rule against backfilling old narration audio, instead reporting it, remains valid.

The artifact inventory (`docs/adr/0062`) and presentation read model recognize only typed provenance on version records: script binding, episode number, requested duration, request summary, and video description (`lib/artifacts/artifact_version_provenance.py`). Introduced in 0.27, these fields are written at generation-task submission and never changed afterward. Earlier video records have only `version / file / prompt / created_at / duration_seconds`, causing every inventory-backfill and presentation-read parse to fail. Backfill silently skips unparseable records as nonexistent artifacts; after upgrading, users see 0/N videos, production status reset to preparation, and preview "selected video presentation unavailable." This violates ADR 0062's "backfilled artifacts are immediately current; users must not be misled into regenerating paid artifacts."

Add a schema-migration step (v12→v13, `lib/project/project_migrations/v12_to_v13_legacy_media_provenance.py`). For each video still referenced by a script entry whose selected version lacks typed provenance, **project all typed provenance fields from project state at migration time and write them back** (`lib/project/legacy_media_provenance.py`), then reproject the entire inventory. Projection uses the same basis builder as freshness comparison (`project_video_basis_components` in `lib/artifacts/media_artifact_currency.py`), sharing registered/expected sources and making backfilled videos immediately current. Freeze a shape of "no reference audio, no voice speakers, no reference-image limit, and only the requested-duration tier"; project provider-audio switches from current project settings. Add `provenance_backfilled_at` to each rewritten record. Read paths do not distinguish these from generation-frozen records; the field distinguishes "projected" from "frozen" only during diagnosis.

In the same step, **register scripts without formal script_plan using no-plan basis** (`structured-content/episode-script-without-plan`, containing only episode number). They remain current until users replan and produce formal plans, when basis changes make script entries stale and return them to the ordinary route. Production status skips plan and plan-confirmation gates for these episodes, evaluating scripts and downstream artifacts directly.

Do not backfill narration audio: its basis includes synthesis-time TTS settings (provider, model, voice, speech rate; see ADR 0062), absent in old records, so every projection would be invented. Include such records in migration reports and report their artifacts missing, matching current behavior.

## Explicitly rejected

- **Relaxing planners and presentation read models to accept old records without typed provenance and compare degraded bases**: adds a branch to every consumer, the third "legacy / unknown provenance" state rejected by ADR 0062, forever burdening read paths. Rewrite data once and preserve read paths.
- **Omitting `provenance_backfilled_at`**: projected and frozen records become formally indistinguishable, leaving no clue when diagnosing "why is this current?" The cost is one field.
- **Failing the entire project migration when projection fails**: one bad record blocks a whole project. ADR 0062 already leaves targets with unprojectable bases unregistered and reports missing on read; follow it, but report skips instead of remaining silent.
- **Using old `duration_seconds` as the sole requested-duration source**: it may be a string or disagree with the entry's current duration. Take the first positive integer in entry duration → project default duration → recorded duration order.

## Consequences

- Migration irreversibly rewrites `versions/versions.json`, first backing up as `versions.json.bak.v12-<ts>` under the same retention rules as other migration backups.
- Backfill covers only videos still referenced by scripts at migration time whose current files match selected snapshots. Projection failures (missing storyboard images, speech-ineligible entries, unresolvable duration tiers, etc.) enter migration reports for users to decide regeneration.
- Freshness of no-plan scripts does not track project metadata changes: editing asset definitions does not make them stale. This intentional simplification means these episode scripts "always match" until plans appear.
- Scripts written before the plan gate also register no-plan basis on save (the planner is the shared registration/comparison entry point). Normal workflows generate scripts after plans; this branch appears only for old data and direct script-save calls.
- The migration chain activates the inventory a second time: v12→v13 performs one complete read-only preflight for every existing project, blocking rejected projects and naming files under ADR 0062. Projects whose inventories runtime can already read pass; rejected data is also unreadable at runtime.
