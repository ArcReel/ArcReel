---
status: accepted
---

# Structured output uses selective fallback, with methods chosen by wire-channel availability

Models natively supporting `structured_output` use native wire parameters (`response_format` / `response_json_schema`) for better speed and accuracy. Fallback triggers only through **advance capability gating** (models not declared supported in the registry, including unregistered ones; conservatively prefer fallback to a native call that errors) or **runtime revalidation** after native HTTP 200, because relays/proxies may silently ignore wire parameters and return prose or violating JSON. Revalidate with `strict=False` to tolerate coercible values and avoid unnecessary paid fallback on valid accepted responses. When falling back, include the native call's tokens in the fallback result. Do not route every backend through fallback: wrapping native support only slows and degrades it; alternatives such as PydanticAI / BAML are too heavy or DSL-incompatible.

Choose by **whether fallback must bypass wire parameters**. OpenAI / Ark use Instructor (pure functions in `lib/backends/text_backends/instructor_support.py`: prompt schema injection, parsing, and validation retries, transparent to upper layers). Gemini injects schema into the prompt in its backend, resends as plain text, strips Markdown fences, validates, and retries with error feedback. Its trigger is precisely relays ignoring wire parameters; Instructor's genai JSON/TOOLS integration still uses response_schema / function calling and would be ignored by the same relay. Prompt schema constraints are the only wire-independent method.

For retry/fallback composition (retry decorators wrap one network call; fallback owns its retries), see `docs/adr/0047`.

## Consequences

- Add one dependency and consult registry model capabilities for gating, paired with `docs/adr/0013`'s model-level declarations.
- Models without native support and relays ignoring native parameters can produce structured results without disturbing native paths.
- Fallback no longer exclusively means Instructor: Gemini's validation/retry logic stays in its backend (`_prompt_json_fallback`), not `instructor_support`.
