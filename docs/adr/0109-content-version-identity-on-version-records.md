---
status: accepted
---

# Content-version identity lives on version records, saved/read through material storage

`docs/adr/0108` assigns provider links to versions and preserves identity across storage migration, without defining its code/persisted representation. Existing history (`versions/versions.json`) groups versions per resource, each pointing to a snapshot; formal files such as `characters/Alice.png` are selected-version working copies. One historical resource is one material, each record one content version, with three independent facts.

- **Identity**: randomly generated project-unique `content_version_id` on write. Selection, restoration, and rename preserve it; trash-restored history keeps identity. Hashes do not determine it: byte-identical records, including copies in other projects, are distinct versions. Cross-project references include project identity.
- **Digest**: `content_digest` from the saved snapshot (bare sha256), recorded for every resource type. Validate reads against it and reject mismatches.
- **Durable locator**: `file`, the local-storage snapshot path. Records without other storage fields mean local locators. Formal files are working copies, not durable locators.

Snapshot saving and pre-restore reads use local material storage in `lib/material/storage.py`; every version-manager write passes this entrance. VersionManager still validates managed snapshot-bucket paths.

## Considered Options

- **Hash identity**: identical copies/reuploads collapse identities, making one side's deletion/unlinking affect another.
- **Resource type + ID + version number identity**: renames change resource IDs and require link/job rewrites.
- **Asset-entry identity/links**: replacement images inherit old links, precisely what `docs/adr/0108` avoids.
- **One schema migration for all historical identities/digests**: reads every old image and competes with other same-schema steps. Only referenced versions need identities; fill on demand.
- **A generic interface with remote implementations now**: remote primary storage is later work; validate interfaces against two real implementations, not premature abstractions.

## Consequences

- Older records lack `content_version_id`, and images also lack `content_digest`. Continue reading/selecting/restoring, without digest validation when absent. `VersionManager.ensure_content_version` fills identity and current-snapshot digest on first reference, then leaves them fixed. No schema migration is added.
- Image restoration now validates content and identifies modified snapshots on failure, matching video/audio.
- This moves only snapshot saving/restore reads. Previews, thumbnails, references, manifests, and archives still read local files; remote work migrates consumers as needed and adds remote locators without reinterpreting existing records.
- Imports retain records and identities from source projects. IDs are project-unique only, never standalone global keys.
