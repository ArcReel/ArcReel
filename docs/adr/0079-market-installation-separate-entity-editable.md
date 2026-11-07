---
status: accepted
---

# Installation records are separate entities; installed endpoints remain editable

Installing a market endpoint creates an ordinary custom-endpoint row plus a binding to source/entry. Store that binding as a separate one-to-one entity with canonical source key, entry slug, installed version, and definition digest, not on `custom_endpoint`. That table's invariant allows only definition-derived read-only mirrors outside `definition` (`docs/adr/0067`); installation is an independently lived fact, not a mirror. Use canonical strings, not source FKs, so removal preserves origin. Only installation records mean installed: matching local author/name does not. Overwriting existing definitions is allowed only without installation records; never transfer installations across sources.

**Installed endpoints remain editable in place.** Built-ins are read-only with Copy as mine, whose copies do not follow upstream. Reusing that for market endpoints would require copying and reattaching models for one header edit. Accept updates overwriting local edits, represented as modified when installed/current digests differ, with warnings and export before update. Market status (latest/update/unavailable) and local modification are orthogonal. **Do not offer unlinking installation records**: update, delete, or continue with the badge.

**Update availability compares only installed and index versions for equality**, not local definition versions/content. Refresh reads indexes, not every definition, and indexes contain no payload digest. Same-version changes intentionally do not show updates. Official review requires content-change version bumps; users trust third parties themselves.

## Amendment to the earlier import decision

Sharing-format design originally said imports retain no origin. Amend this to record installation origins outside endpoint definitions: definitions remain protocol truth and export bytes are unchanged. Local imports still retain no origin.

## Consequences

- Installation is one server transaction, not frontend steps, ensuring uniqueness and that recorded digests match saved definitions.
- Uninstall deletes endpoints and cascades installation records.
- Future market prompts/styles get separate installation records, not this endpoint table.
