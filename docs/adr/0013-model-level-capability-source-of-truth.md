---
status: superseded by ADR-0104
---

# Model-level capability declarations; provider capabilities are a derived read-only view

Models from one provider have different capabilities (Ark lite text models, for example, do not support structured_output, and a top-level declaration would cause native-call errors). Declaring `media_types` / `capabilities` on providers inevitably drifts from individual models. Declare capabilities (the text/image/video capability enums) on `ModelInfo`, at model level. Make `ProviderMeta`'s `media_types` / `capabilities` read-only `@property` values aggregated from all its models, without independent storage. Models own capability truth; provider-level capabilities are non-writable derived views.

## Consequences

- Capability queries aggregate models, providing one truth source. Provider resolution, automatic inference, frontend model pickers, and billing dispatch depend on this data shape.
- Registry declarations are **descriptive metadata**. Runtime backend capability sets determine actual upstream SDK parameters, and may intentionally differ, continuing the declaration/execution separation in `docs/adr/0001`.
- Adding a model requires one capability declaration in its `ModelInfo`, without changing provider-level declarations.
