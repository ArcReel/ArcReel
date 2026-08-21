---
type: "query"
date: "2026-08-21T10:41:39.019792+00:00"
question: "How do character catalog synchronization, global asset persistence, API serialization, and automatic character matching connect?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["sync_character_catalog()", "lib/character_catalog.py", "Asset", "AssetResourceRepository", "routers/assets.py", "test_character_catalog.py"]
---

# Q: How do character catalog synchronization, global asset persistence, API serialization, and automatic character matching connect?

## Answer

Expanded from the original query via graph vocabulary: [asset, alias, catalog, character, match, repository, migration, metadata, source, exact]. The existing graph connected sync_character_catalog() and lib/character_catalog.py to the AssetRepository community, then to the Asset model and routers/assets.py for API serialization, with test_character_catalog.py and its resync preservation test as the behavioral contract. The implementation adds structured alias persistence alongside asset resources, exposes aliases through the asset API and global-asset context, and extends exact same-type matching so canonical names win, unique aliases match, and ambiguous aliases do not auto-link.

## Outcome

- Signal: useful

## Source Nodes

- sync_character_catalog()
- lib/character_catalog.py
- Asset
- AssetResourceRepository
- routers/assets.py
- test_character_catalog.py