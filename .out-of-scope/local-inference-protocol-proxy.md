# Local Inference Protocol Proxy

The main ArcReel repository does not include protocol-conversion proxy scripts/services for local inference servers such as llama.cpp’s llama-server.

## Why this is out of scope

Example request (PR #2445): add a `scripts/` proxy translating embedded-Agent Anthropic requests to local llama-server and rearranging system / developer messages.

Such proxies address one inference server’s changing protocol differences. Including them makes ArcReel responsible for maintenance/security review (including that PR’s CodeQL warning). Agent credentials accept custom `base_url`; fully Anthropic Messages API-compatible (`/v1/messages`) services connect directly. Otherwise maintain the conversion layer as an independent project deployed by its users.

Reopen if a local inference category becomes a major connection path for many users and cannot connect through custom `base_url`. Open a product-support issue then, rather than including conversion scripts.

## Prior requests

- PR #2445 — Add Anthropic-compatible proxy for local llama.cpp agent provider
