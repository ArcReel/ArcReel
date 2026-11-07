---
status: accepted
---

# Keep explicit and snapshot-diff project events; derive projections from registries and make polling read-only

Browsers learn changes only through project events, refetching projects per batch without extra refresh after Agent turns. Hand-picked shadow snapshots covered few character/scene/prop/script fields, missing products, derivatives, settings, episode `source_range`/`hook`, narration/dialogue, `scene_description`, and notes. UI stayed stale after Agent edits. Asset/skeleton additions required manual updates (9e4298a9/a0152134/9c208728), omitted for products/derivatives. Polling also synchronized episode indexes and rewrote migrated scripts, writing during reads only when browsers subscribed.

Decision:

- **Retain both sources.** Explicit events cover invisible facts (same-path bytes, task semantics); diffs catch every other project-state change. Existing deduplication/rescan coordination (2ed330d2) stays unchanged.
- **Registry-derived whole-entry projection.** Read full asset rows, episodes, overviews, script entries, and script-level fields. Settings are all `project.json` except assets/episodes/overview/`metadata`. Exclude only per-write `metadata` and item `generated_assets`: the latter signals image/video readiness, while other changes belong to explicit events to avoid duplicate task-completion notices.
- **Derivatives have separate `本体/衍生` identities.** Match generation events so rescans defer to those batches; exclude derivative tables from base comparisons. Derive capability from asset specs, not hardcoded characters.
- **Polling is read-only.** Stop index synchronization and load projects/scripts without writes.

## Rejected alternatives

- **Every writer reports before/after, retiring polling**: a third source reintroduces explicit/rescan baseline reconciliation and missed/false deletes fixed by 2ed330d2.
- **Declared-field allowlists** (asset specs, `PROJECT_SETTINGS`, item models): system-written `voice_updated_at`/`end_frame_image`/`planning_cursor` are undeclared and still missed. Extra events cost one hint; missing events leave stale UI.
- **Synchronize episode indexes while polling**: The Agent's built-in Write / Edit / Bash cannot touch `scripts/*.json`/`project.json`; every ProjectManager script-write path already synchronizes episode bindings. This synchronization runs only with browser subscribers, showing no workflow depends on it. Direct script-file edits outside the application are unsupported.

## Consequences

- New asset/skeleton kinds are automatically diffed. Frontend still needs routes/copy, covered by exhaustive asset-spec tests.
- Externally added `episode_N.json` entries enter snapshots, but do not automatically register episodes in lists.
