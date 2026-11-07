---
status: accepted
---

# Agent library tools reuse Web orchestration; uploads require explicit user requests and return immediately

Agents need query/upload/select-existing/unlink operations, declared once in `server/agent_toolset/asset_library.py` for both hosts ([ADR 0087](0087-agent-toolset-single-declaration.md)). Handlers call the Web routes' `server/services/project/provider_asset_links.py` and `provider_asset_import.py`. Only `lib/asset_library/links.py` decides links/accounts/scopes. Tool fields match Web responses and errors retain domain-exception i18n keys.

Uploads send user images into provider accounts and may incur costs. After generation rejection, Agents may treat uploads as automatic remediation. Descriptions alone are insufficient: requests require `user_requested=true`; omission returns `invalid_request` without jobs. Tool descriptions, embedded profiles, and external skills agree that rejection authorizes neither upload nor automatic regeneration. This field prompts deliberate calls but cannot prove user consent; explicit conversation instructions remain authoritative.

Upload/review may take minutes or hours. Upload/refresh only save records, wake background processing, and return immediately in both hosts. Embedded tools do not reuse blocking generation-batch waits because provider review has no predictable terminal time and would occupy sessions. Query `get_provider_asset_links` later as needed.

Do not expose provider remote rename/delete or library-account/object-storage/credential-binding configuration: the former belongs in provider consoles, the latter settings. Results contain no keys, relay objects, or signed download URLs.

## Consequences

After requested uploads, Agents need another query to know usability rather than receiving it in one call. Existing-library selection writes are marked `worker`, like other Agent writes. Scope/acceptance: [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
