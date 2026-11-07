---
status: accepted
---

# Layer the data root: projects under `projects/`, without project-identifying prefixes

The former project root became data root directly, flattening projects, DB, global assets, and user memory with `.`/`_` prefixes meaning not-project. Logs/Vertex credentials moved into code or parent directories; unprefixed directories were mislisted and undeletable; project predicates diverged. Layer projects under `<数据根>/projects/<项目名>/`, with unprefixed runtime peers. All ArcReel-managed runtime data stays inside data root, excluding Claude SDK's own configuration (`docs/adr/0074`).

```
<数据根>/
├── projects/<项目名>/
├── global_assets/
├── users/<user_id>/memory/
├── arcreel.db
├── logs/
├── vertex_keys/
├── trial_runs/
└── runtime/
```

- **Nest in place**: preserve configured/mounted/backed-up data roots without deployment changes. Default remains git-ignored repository `projects/`, putting projects at `<仓库根>/projects/projects/`.
- **One project predicate**: only valid names with `project.json` under `projects/` are projects; no other data-root entries qualify.
- **Deny data-root Agent reads by default**: allow current project/current-user memory only; new system directories are protected automatically (`docs/adr/0026`).
- **No downgrade**: old versions misinterpret new directories as projects.

Supersede three ADR-0074 details (`docs/adr/0074`): user memory at `<数据根>/.arcreel/users/…`, `_`-prefixed shared libraries, and `projects_root` derivation. User memory derives from data root; project `.arcreel/` stays unchanged.

## Rejected alternatives

- **Keep flat layout with centralized detection**: every new directory still needs prefixes; logs/credentials lack locations and code roots remain runtime storage, unusable in read-only installations.
- **New default root such as `<仓库根>/data/`**: every Docker/custom mount changes; missed changes appear as lost projects.
- **Hidden system directories such as `.arcreel/trial_runs/`**: hides the same prefix problem deeper.
- **Keep `_global_assets`/`.arcreel.db` names**: obsolete prefixes encourage copying obsolete conventions.
