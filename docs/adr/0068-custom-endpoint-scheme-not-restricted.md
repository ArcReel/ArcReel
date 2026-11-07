---
status: accepted
---

# Custom-provider endpoints do not restrict schemes

Neither custom-provider `base_url` nor request URLs in endpoint definitions restrict schemes: accept `http://` alongside `https://`. Self-hosted services, LAN relays, and local gateways at `http://localhost` are explicitly supported. Requiring TLS would make that endpoint class unusable.

Credentials assigned to an `http://` endpoint (headers or query parameters rendered by `auth`) travel in plaintext. Users accept that risk for configured endpoints; ArcReel does not decide whether a particular internal address needs TLS.

Keep behavior consistent across provider creation/update, discovery, trial runs, declarative submission/polling/secondary retrieval, and artifact downloads, without scheme-based branches. Do not gate individual paths: rejecting `http://` in only one creates contradictory results where discovery succeeds but generation fails under the same configuration.

Credential propagation still follows same-origin rules: strip headers/query credentials rendered by `auth` on cross-origin redirects. Attach credentials to artifact URLs only when same-origin with an endpoint already accessed with credentials in this call. These constraints are scheme-independent and apply to both.
