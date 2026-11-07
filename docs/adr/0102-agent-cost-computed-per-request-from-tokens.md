---
status: accepted
---

# Compute Agent reference costs per request from tokens/catalogs, not SDK-reported prices

Embedded Agent costs use catalog prices/multipliers like generation. SDK `total_cost_usd`/per-model `costUSD` are CLI-local Claude-price estimates, including for non-Claude models, and cumulative under resident `ClaudeSDKClient`. ArcReel commonly uses third-party non-Claude gateways, so these match neither models nor turns.

## Rules

- Price requests using `AssistantMessage.usage`, deduplicated by `message_id` including subagents, each with its context tier and separate 5-minute/1-hour cache writes. Invisible calls such as compaction are turn `model_usage` deltas minus streamed totals, priced as one request.
- Record one provider call per model per turn, summing request costs. This is an accounting-granularity exception only; pricing stays per-request. Skip models without usage; record cache read/write tiers separately without changing total-input meaning.
- Interpret all Anthropic usage (`input_tokens` excludes cache), without gateway-specific correction. Reference-cost positioning accepts differences.
- Use only one Agent-credential multiplier, default one/user preference, never infer subscriptions from presets. Multiplier/binding comes from spawn-time injected credentials, not current active at settlement (`docs/adr/0017`).
- Unmatched model entries are unpriced (`docs/adr/0100`). Do not store SDK prices even as comparisons.

## Considered Options

- **SDK `total_cost_usd` or summed `costUSD`**: wrong Claude pricing and cumulative differencing.
- **CLI `modelPricing`**: managed/hosted-only, no Python SDK parameter, one cache-write price, still needs deltas.
- **Turn-level `model_usage` deltas**: cannot recover request context tiers or split cache writes, underpricing tiered models.
- **Preset gateway-usage corrections**: no real credentials to validate and insufficient value for reference metrics.

## Consequences

- Maintain `model_usage` baselines, starting with first post-resume result, clearing on reset, and treating decreased cumulative values as single-turn usage.
- Background subagent spending belongs to its completion turn, not starting turn.
- Invisible-call deltas are approximate: multiple calls combine context tiers and may overprice. First resumed turns lack baselines and omit those calls.
