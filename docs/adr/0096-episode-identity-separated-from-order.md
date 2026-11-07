---
status: accepted
---

# Separate episode identity and broadcast order: episode numbers become internal IDs

Episode numbers formerly combined identity (paths, item IDs, media filenames, manifests, tasks, links), display name, and broadcast order. Freezing identity froze the others, causing gaps, holes after deletion, no middle insertion, and other-origin episodes between split episodes. Separate all three: retain integer numbers as internal episode IDs, allocated above the historical maximum without reuse; ledger entry order becomes broadcast order. Creators identify episodes by title and position, never internal IDs in visible text.

## Considered Options

- **Renumber on demand**: rewrites all artifact names, breaks links on any failed step, and gaps recur afterward.
- **Opaque IDs**: requires renaming all existing artifacts for the same stability as existing integers.
- **Separate numbering by source origin**: origins change (source-free episodes may gain source), own-source does not mean bonus content, and all lines still need one broadcast order.

## Consequences

- Item IDs carry episode prefixes (`E3S01`) even when their episode is fifth in broadcast order. Creator-visible text shows only the within-episode part, qualified by episode title outside its page.
- Whole-source episodes remain in source order; own-source and source-free episodes may go anywhere, including between splits. Planning changes place them by `docs/adr/0032`.
- Replanning candidates always receive new IDs, never attaching old artifacts by position. Only manual splits preserving the same episode retain identity (see `docs/adr/0032`).
- Historical maxima include every remaining episode-ID record (ledger, disk remnants, tasks, calls), or reuse makes new episodes claim old artifacts/costs.
- Agent tools address episodes by ID; creator explanations use titles/positions.
