# Maintaining the provider API documentation index

This directory stores navigation only: official source links, topics ArcReel depends on, and stable code entry points. Runtime facts belong to registry, backend and endpoint declarations; the runtime never loads them from this index.

The description-source entry is an exception: it supports `provider_desc_<id>` in `lib/i18n/<locale>/providers.py`. Describe only what the platform is. Prefer Wikipedia or Wikidata entries; use official pages when those entries are missing or outdated.

## Change procedure

1. Locate the file by the canonical ID in `PROVIDER_REGISTRY` / `ENDPOINT_REGISTRY`; keep one file per ID.
2. Verify interfaces, capabilities, parameter constraints and billing against official public pages. If ordinary fetching fails, try Jina Reader, `agent-browser`, then the user browser, in that order. Sources must remain official.
3. Update the official entry point, pages ArcReel actually depends on, and code `path::symbol` references for this ID. Shared documents may be linked repeatedly; do not copy page contents or numeric values.
4. When adding or removing registry entries, update the corresponding README here. Completion means every canonical ID is reachable from the README through a same-name index, which links to official sources and the actual consuming code.

Minimal request/response fixtures needed by tests are code contracts, not documentation mirrors. Keep them minimal and include their source. The repository does not store third-party document bodies, full parameter/pricing tables or download scripts for rebuilding mirrors.
