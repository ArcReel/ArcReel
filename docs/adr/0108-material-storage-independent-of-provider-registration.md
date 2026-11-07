---
status: accepted
---

# Model material storage independently from provider asset links

Provider-library and object-storage integration in #2249 change provider-side identity and content location independently. Model material identity, storage, and links separately, with links belonging to specific content versions. Creation references identity, storage saves content, and links express that version's provider identity/usage conditions within a resource scope. Reject mutually exclusive `local / s3 / volcano` types: one material may be object-stored and linked to multiple providers. Combining them entangles storage migration, provider switching, and content replacement.

## Consequences

Changing location alone changes neither content identity nor artifact currency. Version links are independent: selecting image B neither inherits A's links nor invalidates them. Separate unlinking in ArcReel from provider deletion to avoid affecting other consumers.

Unified material requires retrievable actual content for preview, editing, archives, and other providers. Provider-ID-only resources cannot directly serve as ArcReel material. This adds retrieval/storage responsibility but prevents provider assets becoming unusable exceptions in normal creation.

Delivery scope, credential binding, upload states, storage choices, compensation, and acceptance live in [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559) and subtasks. Source evidence and incremental assessment are in [upload research](../research/create-asset-local-upload-research.md) and [object-storage scope research](../research/material-object-storage-scope.md).
