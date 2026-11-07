---
status: accepted
---

# Recoverable deletion, additive restoration, and cross-version recovery through project migrations

Project deletes were hard deletes, structure removal left media per `docs/adr/0003`, and `docs/adr/0080` reserved future trash. Every creator/Agent project deletion now creates a project-trash record; whole-project deletions become deleted projects. Restore adds without overwriting or blocking: retain free identities, allocate new ones on conflicts, never merge with existing content or reject conflicts. Records contain pre-deletion project structure. Like archive import, upgrade old records through the full project migration chain before extracting objects, preserving cross-version restoration without trash-aware migration steps.

## Considered Options

- **Restore objects in place**: each needs conflict rules for reused source ranges, recreated names, and repeatedly edited scripts, potentially worsening state.
- **Recycle files only**: cannot recover actual structured deletions, making trash ineffective.
- **In-place soft deletion**: every reader filters deleted objects while they still occupy names/source ranges.
- **Store only detached fragments**: near-every-release schemas require either file-only restoration or per-step fragment migrations; the chain accepts complete projects only.
- **A separate version-independent trash format**: reinvents serialization that must evolve with project schemas.

## Consequences

- Restored episodes have own source even when originally whole-source splits, no longer occupying whole-source ranges.
- Supersede `docs/adr/0003`'s leave-media-on-remove policy; deleted sheets no longer stay in place.
- Overwritten versions stay in version history, not trash.
- Storyboard/video-unit IDs are the exception: allocate fresh IDs even if old ones are free (`docs/adr/0090`), preventing old clip/crop references attaching to restored entries.
- Agents delete project content only through trash, never direct media/history deletion. Emptying/permanent deletion is creator-only.
