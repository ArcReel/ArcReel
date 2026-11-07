---
status: accepted
---

# Resolve model names against catalog entries on each use without stored mappings; dated snapshots use aliases, unmatched provider models remain usable with runtime fact fallbacks

Custom-provider model discovery returns only model IDs; Agent credentials likewise hold only model names. Both need catalog-entry matches for prices and model facts. Intermediaries use diverse names: namespace prefixes, dotted versions, case changes, or entirely different names, making failed matches common. Normalize names only client-side without changing model identity; catalog-data aliases express whether dated snapshots belong to the same model. Do not store inferred mappings: query the current model catalog each time, persisting only users' explicit entry bindings. Unmatched models remain usable without entries; model facts fall back by name at runtime, without writing guesses into any data.

## Rules

- Compare model names against official model names and aliases after three normalization steps: ignore case; remove the last `/` and preceding namespace; treat dots as hyphens. Exact matches precede normalized matches. At equal match strength, official names precede aliases, because user copies may retain old aliases already separated from official data. Do not remove date suffixes or suffixes such as `:free`, `-thinking`, or `-preview`.
- Custom-provider "fetch models," manually adding custom-provider models by ID, and Agent-credential tier matching share this rule. Built-in providers match their listed API model names exactly (see `docs/adr/0099`).
- Do not store mappings; query the current catalog on each use, using explicit entry bindings when present. Official synchronization introducing entries/aliases changes lookup results without migration or recomputation; frozen costs remain unaffected.
- When one name matches multiple entries (dual-modality models), select the entry matching the media type of the provider model's invocation endpoint. During model fetching, no endpoint exists yet, so use the media type inferred from the name.
- Model aliases arrive only through factory snapshots and official synchronization; users do not edit them. Store user-determined mappings as entry bindings on custom-provider models or Agent credentials, without modifying entries or disabling their official synchronization.
- Custom-provider discovery supplies a default invocation endpoint per model. Matched models take media type from their entries, then select endpoints by name family within that type; if none matches, use that media type's default endpoint under the current discovery protocol. Unmatched models infer both media type and endpoint by name. Users can modify each during fetching.
- Unmatched models can be added as provider models without matching entries, taking media type from invocation endpoints and recording costs as unpriced. At runtime, facts fall back: selectable durations use name-inferred duration presets; other dimensions impose no constraints, leaving upstream rejection for out-of-range requests (see `docs/adr/0018`). Do not write fallback results into capability overrides or other fields; user-set capability overrides still take priority.
- Invocation endpoints attached to provider models determine channel request structures, input capabilities, and metering, rather than name families. Read model facts from matched entries; presubmission estimates, quotes, and settlement metering use the same entry facts, while requests still send API model names. Names serve only entry lookup, endpoint prefilling when adding models, and selectable-duration fallback without matching entries (see `docs/adr/0104`). The sole exception is prompt-length limits for Bailian and Vidu video channels: entries lack that dimension, so determine it from entry official names/aliases, or API model names when unmatched.

## Considered Options

- **Removing date suffixes client-side**: matches more intermediary snapshot names, but same-name snapshots with different dates may have different prices, such as `gpt-4o-2024-05-13` and `gpt-4o`; removal silently charges another price. Which snapshots are the same model as a primary name is a data fact, expressed by aliases generated in the catalog-data repository.
- **Exact matching only**: simplest rules, but common intermediary namespace prefixes/dotted versions make match rates too low, requiring manual bindings for most models.
- **Letting users choose every multi-entry match**: only one dual-modality model currently matches multiple entries; candidate-selection design is not worthwhile for it, and explicit bindings suffice when needed.
- **Choosing default endpoints by the most-used endpoint for that provider/media type**: intermediaries may forward Seedance through `ark-seedance-2` and Kling through `kling-video`; media-type counts would send the latter to the wrong endpoint. Using existing models from the same model vendor instead creates another implicit rule whose reason users cannot see, less intuitive than per-model edits.
- **Writing name-inferred durations into capability overrides when adding**: users would see overrides they never set and later could not distinguish inferred values from their own edits after entries match.
- **Automatically creating user entries for unmatched models**: entry identity requires a model vendor, unknown when unmatched; every added such model would add another user entry to the catalog.
- **Storing matched entry references on addition, then associating only still-unmatched models**: mappings stay stable, but when aliases later become separate entries, stored references keep pointing to the primary name's prices/capabilities. Correction requires distinguishing automatic/manual associations and recomputing after every synchronization. LiteLLM, OpenCode, and Cherry Studio all query by name on use, storing only explicit user choices.
- **Allowing users to add entry aliases**: adding aliases to official entries disables their official synchronization; user mappings already have a storage location.

## Consequences

- Match rates depend on aliases generated by the catalog-data repository, including same-priced dated snapshots and common intermediary names (see `docs/adr/0103`).
- When intermediaries rename known models, users bind an entry once to obtain that model's prices/facts; attached invocation endpoints still determine channel capabilities.
- Official synchronization can change the entry matched to the same provider model, changing prices and facts without a separate notification.
