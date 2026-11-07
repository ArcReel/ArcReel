---
status: accepted
---

# Upload jobs and provider links share records; only user actions change selection

Library uploads are asynchronous: CreateAsset may return before fetching, and only `GetAsset` `Active` is usable. Requests may time out after acceptance. Creating links only after IDs leaves pending/failed/uncertain uploads unqueryable and lets generation silently send ordinary images. From user initiation, an upload is one link record keyed by project, content-version identity, and account/site/region/provider-project scope. States are waiting, submitting, processing, available/failed, plus explicit uncertain submission.

Each version/scope has at most one selected record through a partial unique index. Generation, existing-asset import, and Agent tools query through `lib/asset_library/links.py`, never choose arbitrary records. Only user upload/unlink changes selection; background updates conditionally change state facts only. Image changes during upload preserve the submitted version's ownership; late results after unlink cannot restore links. Reject repeat uploads while a selection exists; unlink first to avoid duplicate creation after uncertainty. Reject mutable asset-entry IDs that mix selection, versions, and accounts and transfer links to new images.

Never recreate uncertain submissions. Every submission has a unique asset name; background queries its group by name to recover IDs. Jobs interrupted mid-submit become uncertain at startup.

Track relay copies separately, retaining by upload status rather than deleting when IDs arrive. With no documented provider fetch limit, URLs last 24 hours. After outcomes, keep copies at least one hour from creation; without outcomes, retain until one hour after URL expiry. Retry deletion with backoff; changed buckets suppress deletion to avoid same-key mistakes elsewhere. These are ArcReel choices, not provider SLAs, adjustable after real-account validation.

## Consequences

Reupload requires unlinking first, but results trace to submitted versions/scopes. Project deletion/restoration rekeys links to tombstone names alongside tasks/calls, preventing same-name inheritance. [ADR 0115](0115-provider-asset-link-scope-frozen-by-cloud-account-identity.md) replaces account record IDs with frozen cloud identity in scope. Scope/acceptance: [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
