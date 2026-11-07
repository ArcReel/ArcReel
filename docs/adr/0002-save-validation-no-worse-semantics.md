---
status: accepted
---

# Script saves use no-worse validation, exempting asset-writeback hot paths

Pure structural validation (`segment`/`scene`/`unit`, `duration`, required fields) previously ran only at archives, never writes. `load_script` only read data through `json.load`, so invalid structures failed deep in workers. Move the single validator to `_write_script_unlocked`, but **not always-strict raising**: reject only valid-before/invalid-after edits introducing errors; accept already-invalid legacy scripts. Entirely skip `generated_assets`-only hot writebacks (`update_scene_asset`, `batch_update_scene_assets`, `update_character_sheet`, reference-video completion). Validate structural Pydantic models selected by `generation_mode`/top-level keys (`Narration`/`Drama`/`ReferenceVideoScript`), **not** filesystem-aware `DataValidator`, which reads disk and rejects valid unfinished drafts without images.

## Consequences

- Accepting already-invalid scripts is deliberate. Prevent this edit introducing errors, not punish schema-evolution history with unrelated 400s. Paths without `load_script`/`before`, such as normalize calling `save_script` directly, fully recompute scripts and can validate strictly.
- Asset writeback on legacy bad scripts remains usable: high-frequency `generated_assets` writes only change `SkipJsonSchema` runtime fields and cannot worsen structure, so full validation adds no value.
- Expose failures **at write time** in manual paths, not after enqueue.
- Scripts/`project.json` have Python APIs (`save_script`, covered here), generation tools (`text_generation`→`ScriptGenerator`, already validated), and raw Agent `Write`/`Edit`. Before `docs/adr/0003`, raw writes passed only `json.loads`/smart-quote syntax hooks and bypassed structural guards. Now `_check_write_access` plus sandbox denyWrite (`server/agent_runtime/agent_access_policy.py`) reject them. In-process tools send `patch_episode_script` through `_write_script_unlocked`, and `project.json` `patch_project` through `ProjectManager.update_project`/`upsert_assets` error-set-diff guards, each with one enforcement point.
- **Do not make validation always strict**: it restores legacy 400s and whole-script hot-write overhead, precisely the avoided outcomes.
- Write validation is separate from provider-agnostic enqueue structure and execution-resolved duration/capability checks (`docs/adr/0001`), implemented separately after provider-resolution convergence (#599).
