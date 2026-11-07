# Translation update and rollback verification

Use only disposable projects. Preserve profile IDs, manifest schema versions, content-mode identities and session IDs. Never force-reset a real project's profile to distribute a translation.

## Profile lifecycle matrix

Test original source -> translated source -> original source with the existing three-way manifest synchronizer:

| Project state | Required result |
| --- | --- |
| New project | Materializes translated built-ins with the same logical files and identities |
| Unmodified existing file | Upgrades normally and updates its recorded baseline |
| User-modified file | Preserves the user's bytes and baseline ancestry |
| User-deleted file | Preserves its deletion/tombstone; translation does not recreate it |
| Project-only file or filename collision | Preserves existing collision semantics and user content |
| Downgrade after an unmodified upgrade | Restores original built-in bytes through normal synchronization |
| Downgrade after a user edit | Preserves the new user edit instead of overwriting it |

Partial retention of Chinese in existing personalized profiles is expected and must not be classified as a failed translation rollout. User memory and project content are never rewritten.

## Sessions and artifacts

The assembler sets `SystemPromptPreset.snapshot=True`. The installed SDK 0.2.173 (declared bundled CLI 2.1.309) documents that the recorded system prompt survives later requests and resume **until compaction or a new session**. Include compaction in the eventual real-model comparison; resuming is not the only instruction-refresh boundary.

Document file-based instruction loading separately. Project `CLAUDE.md` is loaded through the SDK's project setting source, while tools are assembled from the current declarations. Test new and resumed sessions, including old history with translated tool descriptions. Do not assume every file-based instruction shares the append snapshot's lifecycle. Exact skill reload timing remains part of external-runtime evaluation, not a locally proven guarantee.

Changing renderer wording must not invalidate existing artifacts whose formal content is unchanged. Exact provider-request fingerprints may change because they include rendered text. Verify both behaviors independently. Do not regenerate media or conceal a changed request fingerprint by weakening comparisons.

## Release and reversal

Keep instruction batches separate from passive/documentation batches. Reversing Git changes is only the source step: verify the lifecycle matrix after synchronization as well. Report any session-level limitations without restarting sessions or modifying real projects. A locally validated translation remains unreleased for behavioral use until the real-model evaluation in [evaluation.md](evaluation.md) is completed.

For a later authorized rollout or reversal:

1. Retain the source revision and the project's current profile manifest and profile-owned file bytes together. Record customizations, deleted paths and collisions using the existing profile-status API; do not treat mixed-language personalized files as disposable built-ins.
2. Validate the desired original or translated profile in an isolated checkout first. Preserve profile identities, schema versions and content-mode variants. The original source for this candidate is commit `7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250`.
3. Use the existing three-way `sync_profile_to_project` path through the application's normal profile lifecycle. On rollback, supply the original built-in source and the current manifest/user files. Do not replace the project's directory with the source archive, restore an old manifest over later edits, or force a reset.
4. Inspect the resulting manifest/status and compare user-owned bytes and tombstones. Unmodified upgraded built-ins may return to original bytes; files customized before or after upgrade must retain those customizations. Resolve a reported collision through the existing semantics rather than overwriting it.
5. Evaluate new, resumed and compacted sessions separately using the boundaries below. An older append snapshot does not establish isolation for current file-based instructions or tools. Do not regenerate artifacts or resubmit paid requests to finish a profile rollback.

Reproduce the synthetic three-mode, with/without-post-upgrade-edit matrix entirely in disposable paths:

```bash
uv run python -m pytest tests/unit/lib/agent/test_profile_translation_lifecycle.py
```

The six actual baseline/candidate-tree comparisons are recorded in [profile-roundtrip-review.json](profile-roundtrip-review.json); the synthetic test additionally protects collisions, deletions and edits made after upgrade. This procedure documents a later authorized operation; no real-project synchronization or reversal was executed for this candidate.

The SDK source requires CLI 2.1.257 or later for the snapshot option and documents earlier pre-2.1.265 append behavior separately. The installed bundle metadata meets those thresholds; an external/custom CLI still needs its own verification. The assembler comment was clarified to match this documented boundary, without changing `snapshot=True` or session behavior.

The existing English `SessionManager.get_or_connect` documentation was also narrowed to this SDK-documented snapshot lifetime. Its previous unqualified first-turn language statement could imply indefinite isolation across compaction. The clarification retains resident-cache and fresh nonresumable-session behavior, changes no options, and does not equate file-based instruction reload with append lifetime. Independent review compared the installed `SystemPromptPreset` documentation with both source call sites; actual external-CLI/model evaluation remains pending.
