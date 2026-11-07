# Integrations intended to promote commercial services

ArcReel rejects PRs primarily promoting commercial services: adding a third-party built-in endpoint/provider integration, or service recommendations/links in documentation. Discuss such partnerships through support@arc-reel.com, outside code contributions.

## Why this is out of scope

Shipped built-in endpoints only incorporate existing ArcReel implementations (`docs/adr/0067-custom-endpoint-declarative-definition.md`). Expressible long-tail aggregators/relays belong to declarative definitions: users author/import them under invocation endpoints, without code changes. Registering one service makes ArcReel bear release/maintenance costs and sets precedent for equivalent services.

The official market source likewise excludes these submissions (`ArcReel/arcreel-market` `CONTRIBUTING.md` content rules).

Judge the contribution’s primary purpose, not whether commercial services are involved: fixing existing built-in-provider defects or converting existing Python backends to declarative definitions is outside this prohibition.

## Prior requests

- PR #2500 — feat: add MuAPI video endpoint
- PR #3311、#1445 等 — octo-patch’s MiniMax PRs (12 in ArcReel; only #1298, fixing an existing provider, merged); the account submits MiniMax integrations in bulk across GitHub
- PR #3348 — feat(provider): add DreamAPI BYOK image and video endpoints
- PR #3419 — feat(backends): 内置 Higgsfield 供应商，接入 Soul 2 / Qwen Image 3 图片与可灵 3.0 / 可灵 O3 / Seedance 2.5 视频 (built-in Higgsfield: Soul 2 / Qwen Image 3 images and Kling 3.0 / Kling O3 / Seedance 2.5 video; accompanying catalog data at ArcReel/arcreel-catalog#2)
