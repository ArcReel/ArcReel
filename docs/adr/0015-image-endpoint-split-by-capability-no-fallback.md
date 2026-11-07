---
status: accepted
---

# Split image defaults into T2I/I2I capability slots with strict runtime gating and no implicit fallback

Many models in NewAPI/OneAPI relay ecosystems expose generations only, not edits. Reference images route such models to edit calls, causing ambiguous remote 404s. Split the single wildcard OpenAI image endpoint into wildcard / T2I-only / I2I-only endpoints, with `default_image_backend_t2i` / `default_image_backend_i2i` as optional capability-bucket overrides above one default model. Unset slots fall back to the default (see `docs/adr/0054`). At runtime, reference presence strictly selects the path; mismatches raise `ImageCapabilityError` with stable codes. Remove implicit fallback such as unreadable references becoming T2I: early clear failure is preferable to automatic dispatch followed by vague remote errors.

## Consequences

- T2I and I2I can select separate models, with corresponding frontend dropdowns and migrations. Present both as optional buckets per `docs/adr/0054`, not mandatory fields. Endpoint splitting/runtime gating are independent of presentation and remain strict when slots are empty.
- Orthogonal to `docs/adr/0001`: that ADR derives t2i/i2i from execution request form; this one splits defaults by capability and forbids fallback. DashScope / Vidu image backends reuse the same three modes and error codes.
