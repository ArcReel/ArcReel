## Problem Statement

ArcReel 当前的项目智能体会根据项目文件是否存在，自行推断工作流阶段、缺失内容和下一动作。这让工作流事实散落在 Agent 文档、服务端状态计算、MCP 工具与 WebUI 中：会话重启或上下文压缩后可能得到不同判断；合法的空资产集合会被误判为尚未分析；文件存在被等同于完成，无法提示内容已经与其直接上游发生差异；批量生成的部分失败也容易被“已入队”或旧文件存在掩盖。

ChatGPT Pro 给出的 Agent Runtime 重构 profile 已把文档改写为“服务端提供事实、Agent 执行动作”，但该 profile 依赖当前尚不存在的工作流状态、产物 provenance、事务式 step1 编辑和完整的同步迁移能力。直接替换 profile 会让端到端工作流调用不存在的工具，因此必须把代码契约、profile、迁移、REST 与 WebUI 作为同一个目标态交付。

同时，原始代码契约把 stale 当成需要自动补齐的未完成状态，并把模型、分辨率、声音参数和 prompt builder 等生成配置纳入全链路 provenance。这既可能因为追加原文或修改全局配置而让大量既有内容失效，也会诱发非预期重生成费用。ArcReel 需要更局部、更尊重用户已有产物的产物时效模型。

## Solution

建立服务端权威的工作流状态服务，由它统一计算项目阶段、目标集、资产清单完成事实、阻塞项、审核门、产物时效和唯一下一动作。Agent、MCP、REST 与 WebUI 消费同一份版本化输出，不再各自根据文件存在性重建状态机。

为每个派生产物记录局部的内容 provenance：只跟踪该产物直接消费的正式内容及直接上游产物，不构造全项目依赖图，不跟踪 provider、model、分辨率、音色、语速、prompt builder 或其他生产设置。Stale 仅提示现有产物与当前直接内容依赖存在差异；它仍是可用产物，不等于 missing，不阻断 `EXPORT_READY`，也不授权 Agent 自动重生。

引入事务式 MCP 工具写入正式 step1；reference-video 继续沿用隔离草稿晋升。批量工具分别报告本次任务结果与产物状态。现有项目通过既有 project schema 迁移链一次性建立 provenance，安全可读的既有产物直接认定为 current，不引入永久 legacy 分支。

最后统一启用重构后的 Agent Runtime Profile，并在 WebUI 中展示 current、stale、missing、blocked，提供显式重生、受控修复和 profile 重置入口。整个目标态一次发布，不保留新旧双写、feature flag 或长期兼容状态机。

## User Stories

1. As an ArcReel creator, I want the Agent to resume from the same project state after a restart, so that I do not repeat completed work.
2. As an ArcReel creator, I want a new conversation to identify the same next action as the previous conversation, so that workflow progress does not depend on chat memory.
3. As an ArcReel creator, I want the WebUI and Agent to agree on project progress, so that I am not shown contradictory completion states.
4. As an ArcReel creator, I want an empty character, scene, or prop analysis to count as a valid result, so that projects without one asset category do not loop forever.
5. As an ArcReel creator, I want asset inventory completion tied to the analyzed source revision, so that newly added source text is not silently skipped.
6. As an ArcReel creator, I want appending source text to preserve all existing episodes and media, so that extending a story does not invalidate earlier work.
7. As an ArcReel creator, I want the Agent to refresh asset analysis after source text changes, so that newly introduced assets can be discovered before planning new episodes.
8. As an ArcReel creator, I want an episode source edit to flag only that episode’s step1 as stale, so that unrelated episodes remain untouched.
9. As an ArcReel creator, I want a step1 edit to flag only that episode’s formal script as stale, so that the difference is visible without forcing regeneration.
10. As an ArcReel creator, I want an asset definition edit to flag only its sheet as stale, so that the system reports the directly affected design.
11. As an ArcReel creator, I want a shot prompt change to flag only the affected storyboard or reference video, so that unrelated shots remain current.
12. As an ArcReel creator, I want replacing an actually referenced sheet to flag only dependent visuals, so that provenance remains local and understandable.
13. As an ArcReel creator, I want replacing a storyboard frame to flag only its dependent video, so that downstream differences are accurately surfaced.
14. As an ArcReel creator, I want model or provider changes to leave existing artifact status unchanged, so that configuration experiments do not create mass stale warnings.
15. As an ArcReel creator, I want resolution and other production-setting changes to leave existing artifact status unchanged, so that settings apply on the next explicit generation.
16. As an ArcReel creator, I want prompt-builder upgrades to leave existing artifact status unchanged, so that software upgrades do not imply that my media must be regenerated.
17. As an ArcReel creator, I want narration audio to remain usable when narration text or voice settings change, so that the system does not introduce an audio-staleness workflow I did not request.
18. As an ArcReel creator, I want stale media to remain playable and exportable, so that I can intentionally retain an older creative result.
19. As an ArcReel creator, I want stale to be displayed as a warning rather than missing content, so that I understand the difference without being forced to act.
20. As an ArcReel creator, I want a project with stale but present artifacts to reach export-ready, so that existing chosen media does not block completion.
21. As an ArcReel creator, I want stale regeneration to require an explicit selection, so that status checks and ordinary continuation do not create fees.
22. As an ArcReel creator, I want generation without explicit IDs to fill only missing items, so that stale and current choices are preserved.
23. As an ArcReel creator, I want to force-regenerate an explicitly selected current or stale item, so that I remain in control of creative iteration.
24. As an ArcReel creator, I want a failed forced regeneration to preserve the old usable artifact, so that a failed attempt does not erase or misclassify my current work.
25. As an ArcReel creator, I want batch operations to account for every requested ID as succeeded, failed, or blocked, so that partial failures cannot be hidden.
26. As an ArcReel creator, I want task results and artifact status shown separately, so that I can distinguish “this attempt failed” from “there is no usable output.”
27. As an ArcReel creator, I want malformed project data to produce an actionable blocked state instead of a server error, so that I can recover the project.
28. As an ArcReel creator, I want the first error message to be friendly and concise, so that I know what action to take without reading internal details.
29. As an advanced creator, I want technical blocker details available in a collapsed section, so that I can diagnose the exact damaged field when needed.
30. As an ArcReel creator, I want a repair action to open the existing prefilled Agent dialog, so that I can ask the Agent to address the blocker without copying diagnostics.
31. As an ArcReel creator, I want the Agent to repair formal data only through transactional tools, so that attempted recovery does not bypass project integrity guarantees.
32. As an ArcReel creator, I want unrecoverable formal corruption to direct me to version restoration, so that the Agent does not make unsafe direct-file edits.
33. As an ArcReel creator, I want narration and drama step1 edits to be atomic, so that one invalid operation cannot leave a partially edited draft.
34. As an ArcReel creator, I want concurrent step1 edits to detect revision conflicts, so that one editor cannot silently overwrite another.
35. As an ArcReel creator, I want step1 edits to invalidate the existing step1 content review, so that approval remains bound to the reviewed content.
36. As an ArcReel creator, I want full-workflow authorization to cover ordinary step1 review, so that autonomous creation can continue without unnecessary pauses.
37. As an ArcReel creator, I want destructive operations and tool-declared billable confirmations to remain explicit, so that broad autonomy does not authorize materially different risks.
38. As an ArcReel creator, I want reference-video step1 repair to continue using an isolated draft and validation promotion, so that invalid drafts do not replace formal content.
39. As an ArcReel creator, I want product sheets to behave like other asset sheets, so that products do not introduce a separate approval state or confirmation tool.
40. As an ArcReel creator, I want grid generation results reported per scene, so that each requested scene has an understandable outcome even when scenes share one grid image.
41. As an ArcReel creator, I want the UI to show current, stale, missing, and blocked counts, so that project status reflects the server’s authoritative facts.
42. As an ArcReel creator, I want stale items to expose an explicit regenerate action, so that I can update only the media I choose.
43. As an ArcReel creator, I want existing project completion metrics to continue counting usable stale files, so that upgrades do not make completed projects appear unfinished.
44. As an ArcReel creator, I want old projects to upgrade without mass regeneration prompts, so that opening an existing project is safe and cost-free.
45. As an ArcReel creator, I want imported old project archives to receive the same provenance migration as startup projects, so that import and local upgrade behave consistently.
46. As an ArcReel creator, I want user-customized project Agent profiles preserved during upgrades, so that my deliberate instructions are not silently overwritten.
47. As an ArcReel creator, I want project settings to identify customized legacy profile files, so that I understand why the new built-in workflow was not applied.
48. As an ArcReel creator, I want an explicit reset-to-built-in-profile button, so that I can adopt the new workflow without manually deleting files.
49. As an ArcReel creator, I want reset to describe the customized files it will discard, so that profile reset is an informed destructive action.
50. As an ArcReel creator, I want common Agent instructions with mode-specific projected references, so that narration, drama, and ad share one process without losing their domain differences.
51. As an ArcReel creator, I want the workflow Agent to execute a server-provided stable action rather than inspect filenames, so that profile behavior remains predictable.
52. As an ArcReel creator, I want Agent completion reports to list current, failed, and blocked requested IDs, so that “queued” is never presented as “generated.”
53. As a maintainer, I want all Agent skill and subagent frontmatter parsed as YAML, so that quoted, colon-containing, and multiline metadata works correctly.
54. As a maintainer, I want malformed profile frontmatter rejected by static lint, so that broken skills do not reach users.
55. As a maintainer, I want profile pointers checked after each content-mode projection, so that every disclosed reference exists in the materialized project profile.
56. As a maintainer, I want MCP names used by profile examples checked against the registered tool set, so that profile and runtime cannot drift silently.
57. As a maintainer, I want obsolete CLI flags and forbidden direct-write instructions rejected by profile lint, so that removed behavior does not return through documentation drift.
58. As a maintainer, I want profile eval files validated for parseability and unique IDs, so that behavior evaluation failures are deterministic.
59. As a maintainer, I want user-modified profile files preserved by the existing three-way materialization semantics, so that the runtime upgrade respects local ownership.
60. As a maintainer, I want unmodified legacy variants and obsolete agents removed during materialization, so that projects do not expose duplicate workflow skills.
61. As a maintainer, I want the current compose implementation preserved unless the replacement is functionally superior, so that a documentation refactor does not regress a low-frequency media path.
62. As a maintainer, I want the new profile enabled only after its workflow tools exist, so that no released profile calls unavailable interfaces.
63. As a maintainer, I want one workflow-status schema shared by service, MCP and REST, so that new consumers cannot invent a second state model.
64. As a frontend maintainer, I want translated status and recovery copy in Chinese, English, and Vietnamese, so that the new user-visible states follow ArcReel’s i18n contract.
65. As a Windows user, I want manifest and migration file operations to use portable path, lock, encoding, and atomic-write behavior, so that project upgrade works outside POSIX-only environments.

## Implementation Decisions

- Introduce one authoritative workflow-state domain service. It reuses the project manager, status calculation, episode ledger and planning cursor, existing script-review logic, generation-route skeleton rules, asset-inventory completion and artifact provenance. MCP and REST expose the same versioned response model; WebUI consumes that response.
- Keep the existing content-mode and generation-route meanings. Narration and drama choose target episodes from the episode ledger rather than filenames. Ad remains a single-episode content mode and skips the step1 workflow.
- Return the first unmet workflow condition as the state and a single stable domain action as `next_action`. Code returns action identifiers and arguments, not profile paths or subagent names. Blockers take precedence over ordinary actions.
- Remove the proposed product-review workflow stage, product-review persistence and product-review confirmation tool. Product sheets use the same artifact lifecycle as character, scene and prop sheets.
- Persist asset-inventory completion independently from asset bucket contents. The record identifies the analyzed source scope and source revision. Only a current all-source completion record unlocks the complete narration or drama workflow; scoped analysis remains partial.
- A source revision is computed deterministically from safe, normalized project-relative source files and project source semantics. Derived per-episode source files are not double-counted in the all-source inventory revision. Source path escape, symlink, unreadable files and malformed scope return blockers rather than being skipped.
- Refreshing asset inventory after a source change is additive and does not mark existing episodes or media stale. Appending new source material leads to asset refresh and episode planning for new content.
- Add a project-local artifact manifest with versioned deterministic serialization, a dedicated lock, project-root containment, symlink protection, atomic replacement and unchanged-write avoidance. The manifest is runtime-owned and outside Agent direct-write permissions and exported script content.
- Artifact keys are built through typed or reversible builders rather than parsed hand-built strings. Keys cover asset sheets, episode step1, episode scripts, grids, storyboards, videos and audio presence.
- Content provenance is local to each artifact. It covers: an episode’s source and step1; step1 and its formal script; an asset definition and its sheet; shot content plus actually referenced sheets and the shot’s storyboard or reference video; and a storyboard frame plus its corresponding video.
- Content provenance does not include provider, model, credentials, endpoint, resolution, aspect-ratio or grid production settings, voice, speed, prompt-builder versions or other execution configuration. Narration audio has presence/current/blocked semantics but no content-stale transition.
- Stale is a usable warning. It does not block later workflow states or `EXPORT_READY`, and it does not become an implicit next action. Backward-compatible `completed` counts current plus stale usable artifacts, while stale receives its own count.
- Generation calls with omitted IDs select missing items only. Explicit IDs select valid current or stale items for forced regeneration. An explicitly empty collection is invalid, and unknown IDs are reported rather than ignored.
- A failed regeneration preserves the old file and manifest entry. The old artifact retains the status implied by its existing content provenance. Failure is recorded only in the task result.
- Batch execution results exhaustively partition requested IDs into succeeded, failed and blocked. Artifact currency is re-read independently after execution; task success is never inferred from file presence or current status.
- Grid provenance preserves the group artifact and its scene membership. Workflow, task completion and UI results are projected per requested scene ID, including per-scene cut failures.
- Collection artifacts expose a collection-level blocked state when their container cannot be safely enumerated. In that case no IDs are guessed into current, stale or missing; blockers include a machine-readable field location and reason.
- User-facing blocker presentation uses localized actionable summaries, such as asking the Agent to repair damaged file structure, with technical details collapsed. The repair action reuses the existing prefilled-dialog mechanism and passes structured blocker details into the conversation.
- Agent repair preserves the write boundary: formal structured data is changed through transactional MCP tools, while only tool-issued invalid isolated drafts may use file editing. When formal corruption prevents transactional repair, the Agent directs the user to restore a prior version.
- Add an atomic, revision-checked step1 patch interface for narration and drama. Operations update, insert, move or remove items in memory, validate the whole current schema and content constraints, and commit all-or-nothing under the same project/episode lock used by Web saves. Reference-video remains on open-isolated-draft then validate-and-promote.
- Reuse the existing content-fingerprint-bound step1 review. A successful step1 edit naturally invalidates review. Explicit full-workflow autonomy may satisfy ordinary content review, while destructive operations and tool-declared billable confirmations remain explicit.
- Extend the existing project schema migration chain by one version. During that single migration, every safe existing formal artifact is recorded against its migration-time local content and treated as current. No legacy marker, lazy read migration or permanent legacy state is introduced. Project startup and archive import reuse the same migration.
- Replace line-oriented skill metadata parsing with safe YAML parsing. Metadata must be an object with non-empty name and description, boolean user-invocable defaulting to true, and consistent variant identity. Invalid metadata is warned and skipped.
- Add one static profile lint command to validate skill and agent frontmatter, all three mode projections, pointer targets, registered MCP names, forbidden obsolete strings, eval JSON parsing and eval ID uniqueness.
- Replace the three top-level mode prompts with one common prompt and mode-projected workflow references. Replace three workflow skill variants with one common workflow skill. Split generation guidance into narrowly triggered references for routing, completion, image edit/regeneration, reference-draft repair and duration confirmation.
- Rename the generic generation subagent to a task-oriented subagent that receives fixed requested IDs and reports exhaustive outcomes. Remove provider-specific prompt prose already owned by backend code.
- Preserve the repository’s current compose implementation because the supplied replacement has no functional improvement; update only its Agent-facing skill instructions.
- Use the existing profile materialization state machine for variants-to-common migration. Unmodified obsolete files are removed, user-modified files are retained, and incomplete or empty source profiles continue to fail closed.
- Add a project-settings profile status and reset operation. Customized legacy profile files are listed; reset is an explicit destructive action that restores the current built-in profile after warning which local modifications will be discarded.
- Add an authenticated workflow-status REST endpoint that shares the MCP response model. Update current project progress and generation UI to render current, stale, missing and blocked, expose explicit stale regeneration, and reuse the existing Agent-dialog entry for repair.
- Keep all new user-facing text in the existing Chinese, English and Vietnamese translation namespaces. Agent-only MCP diagnostics remain untranslated until projected into user-facing REST/UI messages.
- Deliver the code, migration, MCP/REST contract, profile, evals and UI as one target-state release. Internal implementation order may be incremental, but the product does not contain a long-lived feature flag, dual-write path or old workflow adapter.

## Testing Decisions

- The primary seam is the public behavior of the authoritative workflow-state service. Integration tests construct complete temporary projects and assert the versioned workflow response, target episode, artifact classifications, blockers, gates and next action. Tests do not inspect private helper calls.
- The same workflow-state fixtures cover narration, drama and ad; storyboard and reference-video routes; grid and per-item storyboards; empty asset buckets; appended source; planned, consumed and stale ledger entries; malformed structured data; and export-ready projects with usable stale artifacts.
- Provenance tests assert observable locality: edits affect only direct dependents; appended source does not affect prior episodes; execution-configuration changes do not change artifact currency; narration audio does not become stale; stale remains usable and non-blocking.
- Migration integration tests extend the existing project-migration runner tests. They verify schema advancement, backup and failure isolation, idempotence, direct-current backfill without a legacy field, unsafe artifact rejection, imported archive migration and no mass-regeneration state after upgrade.
- Artifact-manifest unit tests cover deterministic serialization and hashing, typed key construction, project containment, symlink rejection, lock behavior, atomic writes, unchanged-write avoidance and Windows-safe filesystem behavior.
- Generation-tool integration tests exercise the public MCP tools with real temporary project state and the queue test doubles already used by SDK tool tests. They verify missing-only default selection, explicit current/stale regeneration, unknown and empty-ID errors, and exhaustive succeeded/failed/blocked results.
- Worker writeback tests assert that manifest state is updated only after a formal output file is committed successfully. Failed tasks preserve prior files and prior artifact status.
- Step1 patch integration tests use the existing script-review and project-lock seams. They cover revision conflict, each operation, whole-batch rollback, schema and source-fidelity validation, review invalidation, preservation of downstream files, and serialization against concurrent Web saves.
- Profile materialization tests extend the existing manifest state-machine suite. They cover common prompt and workflow upgrades from all three variants, projected mode references, removal of unmodified obsolete files, preservation and detection of modified files, reset behavior, one visible common skill and fail-closed sources.
- Profile lint tests run the static command against the shipped profile and targeted invalid fixtures. They cover quoted, colon-containing and multiline YAML; malformed frontmatter; metadata drift; hidden skills; broken pointers; unregistered MCP names; obsolete flags and direct-write instructions; invalid eval JSON and duplicate eval IDs.
- REST tests assert authentication and exact schema equivalence with the workflow service. MCP tests assert the same domain response serialized as tool output. Neither layer re-tests the state machine independently.
- Frontend component tests use workflow-status fixtures to verify current/stale/missing/blocked presentation, usable completion counts, explicit stale regeneration, friendly blocked summaries, collapsed details, prefilled repair dialog and customized-profile reset confirmation.
- i18n consistency tests remain the acceptance seam for Chinese, English and Vietnamese key parity.
- Behavior evals use the supplied optimized evals as a baseline, revised to reflect missing-only default generation, non-blocking stale, removed product review, transactional step1 edits and the registered workflow-status tool.
- Full acceptance runs backend unit/integration tests, Ruff, formatting, basedpyright, import-linter, frontend lint, frontend typecheck/Vitest and production build. Existing test classification rules remain enforced: fast isolated tests are unit, real cross-module temporary-project tests are integration, and no external paid generation is required.

## Out of Scope

- Changing the business meaning of content mode or generation route.
- Making generation route mutable after project creation.
- Adding a third generation route for grid storyboards.
- Changing media-provider APIs, provider capability registries or backend request construction.
- Tracking provider, model, endpoint, credentials, resolution, aspect ratio, grid setting, voice, speed or prompt-builder versions in artifact provenance.
- Marking narration audio stale when narration text or voice settings change.
- Automatically regenerating stale artifacts.
- Requiring every artifact to be current before export.
- Adding a product-sheet fidelity review state, confirmation tool or hard gate.
- Replacing or productizing the compose-video media algorithm and CLI.
- Allowing Agent file tools to directly edit formal project, script or step1 structures.
- Replacing the existing reference-video isolated-draft repair and promotion workflow.
- Introducing a long-lived feature flag, dual-write system, old workflow adapter or lazy legacy branch.
- Redesigning the overall project UI beyond status, recovery, regeneration and profile-reset interactions required by this spec.

## Further Notes

- The supplied refactor package is based on the current runtime code baseline; the repository has only an unrelated skills-sync commit beyond that baseline.
- The supplied profile is a target-state replacement, not a standalone drop-in. It must not be enabled before workflow status, asset-inventory completion, transactional step1 editing and local provenance are available.
- The project glossary and ADRs record the agreed vocabulary and deliberate deviations from the supplied code contract: migration backfills existing artifacts directly as current; product sheets share the normal artifact lifecycle; provenance tracks local formal content only; stale remains usable; and task results are independent from artifact currency.
- The original code contract’s product-review stage and tool, legacy-backfill field, configuration-sensitive hashes, missing-plus-stale default generation, and all-current completion requirement are intentionally superseded by this spec.
