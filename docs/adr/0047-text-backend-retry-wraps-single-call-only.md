---
status: accepted
---

# Text-backend retries wrap only a single network call; fallback orchestration stays outside and has one retry layer of its own

The structured-output fallback mechanism (`docs/adr/0014`) means one `generate` may contain multiple billable calls: a native call plus several fallback attempts after revalidation fails. Previously, all three text backends (gemini / openai / ark) decorated their entire `generate` with `@with_retry_async`, placing fallback calls inside its retry scope. This was intentional ("transient errors on either native or fallback paths are uniformly handled by the outer retry"), but has two costs: **multiplicative replay**, where a transient fallback-attempt failure triggers an outer retry that also replays the successful, already billed native call, with worst-case geometric amplification of outer retries × fallback attempts × inner retries; and **false string matches**, where retry decisions in `lib/infra/retry.py` match transient patterns in message text ("429" / "500" / "timeout", etc.) as well as types. Exceptions thrown after fallback exhaustion interpolate dynamic model-side text (validation-failure reasons and output fragments), which can accidentally match a pattern and trigger replay of the entire flow.

Split all three backends consistently: `generate` becomes an **undecorated orchestration layer** (revalidation + fallback scheduling), and `@with_retry_async` decorates only the **single-network-call method** (gemini `_generate_native`, openai `_generate_native`, ark `_call_chat_completions`). The fallback path has one retry layer with the same configuration (the gemini fallback loop directly calls the retry-enabled single-call method; openai / ark Instructor fallback functions are decorated, since the contract of `instructor_fallback_*` is to perform no transient retries itself and have callers supply them). Exceptions from revalidation and fallback orchestration no longer pass through any retry decision. New text backends should follow the same structure: retry decorators appear only on single-network-call methods.

Two accompanying details: openai signals schema incompatibility with `None` (rather than an exception) from its retry-enabled single-call method. Proxies may wrap upstream schema errors in status codes such as 429; an exception passing through the decorator would be misclassified by string patterns as transient and retried needlessly. The `None` signal preserves "zero retries for schema errors, immediate fallback." Ark's catch-all `except Exception` fallback for native structured calls is existing behavior (covering intermediary response shapes such as empty choices that error-message allowlists cannot predict), and is not narrowed by this decision.

**Explicitly rejected**: retaining one outer retry around the entire flow. The fallback layer's own retries equivalently preserve the benefit of "transient fallback-path errors can also be retried," while splitting is necessary to remove both multiplicative replay of billed calls and false string matches.

## Consequences

- Each layer's retry budget is independent and bounded: worst-case call count = native retry limit + fallback-attempt count × fallback-layer retry limit, without geometric multiplication.
- Each of the three backends has a regression test fixing this structure: "transient fallback failure does not replay the native call."
- Ark native structured calls thereby gain transient retries at the single-call layer (previously, transient failures immediately fell back to Instructor), reducing fallback counts.
- Residual risk: if exception messages from exhausted validation inside Instructor happen to contain transient-pattern text, the fallback path itself may still replay (validation occurs inside the library and cannot be separated from retry scope), but successful native calls are no longer affected.
