# Generation results

Generation tools return structured per-ID results. Read the structure rather than summarizing the human-readable text.

Selection has three meanings:

- A non-empty ID list explicitly selects work and may force regeneration.
- An omitted ID field means fill missing work only.
- An empty ID list is invalid; omit the field instead.

The requested IDs are partitioned into `succeeded`, `failed`, and `blocked`; `skipped` records reused work outside that requested partition. Report every ID with its structured problem code and action. Do not retry by interpreting prose.

Keep four independent axes separate in status reports:

- workflow step state;
- task or queue state;
- provider submission checkpoint;
- artifact state (`current`, `stale`, `missing`, or `blocked`).

A successful task does not prove that an artifact matches current inputs. A missing artifact does not prove that a paid task failed. A provider-submitted task may already be billable. A blocked artifact state must not be treated as missing and regenerated.
