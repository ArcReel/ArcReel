---
status: accepted
---

# Preserve content during migration/import: input gaps block generation, not backfill; reject whole projects only for unreadable or unsafe data

Backfill formerly judged old artifacts by current generation inputs. Empty descriptions, missing referenced sheets, or mixed speech prevented basis projection and registration, making paid usable content missing after upgrades. Migration steps independently rejected structural anomalies, while reports stayed inaccessible in APIs. Rules blocked data harmless to actual use.

Decision:

- **Scope.** Support data written by prior normal flows. Do not specially defend user-edited anomalies, but do not let them collapse whole projects.
- **Gaps block generation only; basis still projects.** Record missing inputs as absent, preserving complete-input basis and existing entries. Register usable formal files as current during upgrades; imports preserve valid frozen claims (`docs/adr/0062`). Filling gaps later makes them stale normally. Backfill mixed-speech videos too (`docs/adr/0089`) while retaining replanning state. Missing historical TTS settings are not gaps: they define delivered audio and cannot be computed (`docs/adr/0075`), so remain unregistered.
- **Never delete/discard user content.** Retain unreadable files without registration; repair deterministic structural defects, leaving unrepairable content intact.
- **Normalize normal historical shapes rather than reject.** Preserve displaced alternatives as backups. Add placeholders for undefined imported assets.
- **Whole-project data rejection has only two classes:** unreadable project/script/version/manifest data (invalid JSON or missing shape-defining content such as content mode), and unsafe archives. Incomplete migration from disk/write failures is not a data verdict: retain blocking, preserve projects, and retry after repair (`docs/adr/0022`).
- **Reports contain only changed/lost content:** normalization choices, placeholders, and artifacts not carried over. Exclude generation gaps (generation explains them) and persistent replanning states. Nonempty upgrade reports notify once; imports request informed confirmation once.

## Rejected alternatives

- **Register generated sheets by bytes like uploads**: loses description-based currency and adds source branches; projectable basis already restores normal registration.
- **Mark backfills stale or awaiting confirmation**: `docs/adr/0062`'s rejection remains; gaps are not age, and all readers need another branch.
- **Per-item report confirmation/editing**: imports need one consent, not another workflow.
- **List all gaps**: lengthy irrelevant upgrade lists bury actual content changes.

## Consequences

- Narrow `docs/adr/0062`: unregistered projection means genuinely uncomputable basis; whole-project backfill blocking means incomplete migrations only. Supersede `docs/adr/0075`'s user-decided regeneration for projection failures, retaining old-audio rules; supersede `docs/adr/0073`'s missing expected basis under gaps, retaining generation rejection; supersede `docs/adr/0004`'s undefined-asset import rejection; narrow `docs/adr/0064`'s any-corrupt-file verdict, preserving corrupt drafts.
- Previously migrated projects need one additional backfill for gap-skipped artifacts, without changing existing entries.
