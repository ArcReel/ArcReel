---
status: accepted
---

# Configure text backends by task tier: simple / complex with default fallback

Text-pipeline (TextGenerator) call sites cover script generation, episode planning, script_plan content extraction / splitting (drama / narration / reference_video), project overviews, and style-image analysis, and continue growing as features evolve. If model settings correspond one-to-one to call sites, the configuration surface grows linearly: each new call site either adds a setting or becomes a model source outside existing settings and invisible to users. Configure by **task tier** instead: code fixes each call site to either **simple** or **complex** (simple = project overviews and style-image analysis; complex = script generation, episode planning, and all script_plan tasks). Users configure only "which text backend each tier uses" plus a fallback "default model"; an unset tier falls back to the default model, and an unset default model uses automatic inference. Resolution is **project-first**: project tier > project default model > global tier > global default model > automatic inference. "Project default model" means "use this for this project as a whole": a more specific project tier can override it, but it shadows global configuration.

## Explicitly rejected

- **Configuring separate keys by task type**: settings grow uncontrollably with call sites; in practice, new call sites do not receive settings in step, eventually leaving some call sites with invisible, unconfigurable model sources.
- **Three tiers: simple / medium / complex**: current call sites have no stable distinction between "medium" and "complex," and two tiers cover all actual decision needs. The number of tiers is itself configuration surface, so keep it constrained.
- **Mapping tiers to Claude Agent SDK models as well**: Agent SDK accepts only Anthropic-protocol-compatible credentials, whereas tiers can point to any text_backends provider. Combining them would create the invalid state "the tier selects a model the agent cannot use." Agent-conversation and subagent-reasoning models remain determined by agent-provider configuration (see `docs/adr/0017`); the settings page explains this boundary beside text tiers.
- **Dimension-first resolution** (project tier > global tier > project default > global default): once global tiers are fully configured, the project default model never takes effect, making that field meaningless.

## Consequences

- Tier mappings are code facts that users cannot change: every new text call site must explicitly belong to a tier (the TextTaskType → tier mapping has exhaustiveness checks). Text-generation calls must not bypass tier resolution.
- The simple tier handles style-image analysis, so its model must support image input (vision). Fail immediately during resolution if capability is insufficient; do not silently switch models.
- Settings pages display a persistent explanation listing the call sites covered by each tier (a shared-source component in global settings / project settings / the creation wizard). Coverage changes must update the explanatory copy in step (zh / en / vi).
