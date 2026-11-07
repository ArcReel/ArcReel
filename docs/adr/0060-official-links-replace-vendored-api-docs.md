---
status: accepted
---

# Index official third-party API documentation links instead of mirroring document bodies

ArcReel no longer commits provider API or Claude Agent SDK documentation bodies, complete parameter/price tables, or mirror-download scripts. Media providers and endpoints maintain official links and stable code entry points by canonical ID; SDK development queries official online documentation and uses official verifiers. Registry, backend, and endpoint declarations continue to own runtime capabilities and numeric values. Indexes serve only source discovery and verification navigation.

This reduces copy drift after upstream updates and repository maintenance, at the cost of offline bodies, stable line numbers, and full historical-page reproduction. Use pinned upstream source or repository contract tests for exact historical semantics. Minimal request/response fixtures needed for tests may remain, but must not grow into third-party documentation copies.
