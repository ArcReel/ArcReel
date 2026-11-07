---
status: accepted
---

# Freeze cloud-account identity when scoping provider asset links

Provider asset IDs are valid only in their creating cloud account. Library-account records are editable in place: Access Keys may change accounts without changing record ID/site/region/project. [ADR 0111](0111-asset-upload-job-and-link-share-one-record.md) used record ID plus site/region/project, allowing old IDs after replacing keys and reconfirming; pending jobs would also submit/query with new keys. Amend only that scope composition.

Links/jobs freeze cloud-account identity at creation. Scope is identity, site, region, and provider project, excluding account record ID. Selected uniqueness, generation selection, upload/import duplicate checks, settings-account matching, and background progression all use these four. On account-changing key replacement, retain old scopes without deletion/rewrite or applicability to new identities. Unsubmitted jobs fail with `account_scope_changed`; processing jobs stop automatic polling. Imported links follow the same rule.

Identity is a version-prefixed SHA-256 of site and Access Key ID, excluding Secret Access Key and raw keys. Secret-only changes preserve identity. Do not use actual account IDs yet: Volcengine STS `GetCallerIdentity` (Version 2018-01-01) can return AccountId, but its host is unregistered, adds save-time network calls, and subusers may lack permissions; corresponding BytePlus official docs were not found. Until validated with real accounts, offline digests are conservative and only stricter.

## Consequences

Same-account Access Key rotation or switching subusers invalidates applicability and requires upload/reselection even though IDs remain valid. This is the digest tradeoff, without cross-account misuse. Duplicate records of one cloud identity share links. Alembic backfills current recorded-account identities; deleted/changed-scope links get values matching no account. Verified future account APIs may replace digests and their prefixes. Scope/acceptance: [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
