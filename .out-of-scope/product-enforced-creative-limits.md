# Product-Enforced Creative Limits

ArcReel does not impose product-level hard limits on scripts’ **creative dimensions**, such as character count per storyboard/grid.

## Why this is out of scope

Example request (#947): after 800+ generated images, a user observed more retries when a shot references >3 images (character1 + character2 + scene), requesting **enforced** ≤2 characters per shot during normalization.

We reject product-enforced creative limits because:

- **Users own creative choices**: character counts and shot arrangement are narrative decisions. Hard truncation sacrifices legitimate ensembles, confrontations, and gatherings, turning generation stability engineering into creative restrictions.
- **Fix stability at generation, not by limiting inputs**: poor success rates with many references are image/grid engineering problems. Improve generation/retry strategies or fallback mechanisms (e.g. grid history/uploads, #975), rather than prohibiting expression upstream.
- **Personal preferences use semantics, not global enforcement**: fewer characters per shot is a *personal preference*. Users tell the Agent in natural language for soft adherence, overridable by content needs, not every-project enforcement. `default_duration` already follows that pattern: persisted preferences, soft preprocessing-subagent adherence, content overrides permitted.

Product-enforced per-shot character limits are therefore out of scope. **Personalized semantic preferences during Agent episode generation** (natural-language injection + soft adherence) are a viable alternative tracked in #976.

## Prior requests

- #947 — 「建议把单分镜人物控制在 ≤2 降低宫格/分镜图抽卡率」 (suggest ≤2 characters per shot to reduce grid/storyboard retries)
