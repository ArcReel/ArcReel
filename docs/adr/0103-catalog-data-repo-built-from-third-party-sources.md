---
status: accepted
---

# Build model catalogs automatically from third-party inputs in a public repository; instances synchronize fully automatically without official services

Synchronizing metadata lets prices/new models follow vendors without waiting for releases. No third-party source alone suffices: text coverage is good, Chinese-vendor image/video/TTS coverage scarce, and formats differ. Publish ArcReel-format data from a public catalog-data repository, the sole official synchronization/factory-snapshot source. Scheduled builds combine third-party inputs with manual data; instances automatically check/apply updates. Text freshness follows upstream, while people maintain only missing portions.

## Rules

- Third-party sources are build inputs only, never read directly by instances. Manual data overrides automatic results.
- Generate built-in text-provider lists through upstream→built-in mappings; manually maintain only filters/few additions/removals. Maintain media lists manually.
- Build aliases for dated snapshots sharing primary prices/capabilities and intermediary names in third-party data. Matching rules: `docs/adr/0105`.
- Publish only after the main repository's shared validator passes; failed builds publish nothing.
- Instances fetch raw URLs only, reusing market GitHub proxy prefixes without instance identifiers (`docs/adr/0078`, `docs/adr/0093`). Official services are outside synchronization; disabling them has no effect.
- Apply newer dataset versions automatically as whole batches without confirmation; skip entries whose official synchronization is disabled.
- Tolerate data under reference-cost standards: ignore unknown fields; pricing depending on unknown selection dimensions becomes unpriced under `docs/adr/0100`; reject wholly unparseable datasets for that attempt. No schema version.
- Official provider models may declare minimum application versions for newer channels. Older instances list them with required versions but never enable, nominate, assemble, or invoke them; defaults pointing at them are treated as unspecified. Previously released old versions still hide them. Because unknown fields are ignored, minimum-version fields must exist from the first data format so every synchronizing instance enforces them.
- Official provider models declare attached built-in endpoints. Request structures are release-time code facts; model→endpoint bindings are synchronized data.
  - Manual build inputs may omit endpoints, filled with provider/media defaults. Published rows explicitly require endpoints under strict validation.
  - Before endpoint-declaration support, instances still select by API model names.
- Endpoint catalogs live in `arcreel_catalog_core` as copies of code endpoints, enabling application-independent catalog builds/validation. Include:
  - Every attachable built-in endpoint's name, media type, optional introduction version;
  - Per-provider/media available/default endpoints;
  - Application version introducing endpoint-declaration reading.

  ArcReel tests enforce consistency with registries/provider declarations. Strict validation checks four things: published models declare endpoints; names exist; media matches entries; endpoints are available for that provider/media. Instances still accept missing/unrecognized names normally.
- Derive published models' minimum versions as the maximum of:
  - Attached endpoints' introduction versions;
  - Manual values;
  - Endpoint-declaration-reading version whenever provider/media has multiple endpoint choices, regardless of default selection. Old name-based logic may misroute even newly declared defaults.

  Endpoints present at catalog introduction are baselines without introduction versions because old behavior was already correct. Existing models are exempt from the third condition after verifying name-based selection matches declarations.
- Introduction versions use the next patch after `.release-please-manifest.json` at main merge, such as `0.35.1` when manifest is `0.35.0`. Endpoint-declaration support uses the same rule.
  - Actual next versions are unknown before merge: release-please chooses patch/minor by commits afterward. Every later release is at least next patch and every current release below it, excluding all unsupported releases without blocking the first supporting one.
  - Main preview images still report previous releases and cannot enable new-endpoint models, conservatively.
  - Never rewrite introduction versions on later releases. If releases occur before merge, refill from updated manifests. Tests reject values above next patch but cannot catch stale values not updated after releases; premerge review must.
- Official data follows published ArcReel versions, not main. Builds read `ArcReel/ArcReel` latest release, holding models whose derived minimum exceeds it and releasing the rest, avoiding prompts to upgrade beyond downloadable releases.
  - Unreadable latest releases fail builds and preserve prior data, without releasing held models.
  - Holding provider defaults also fails builds with reasons, without substitution. Catalog PR checks warn only; after merge, publication waits for corresponding ArcReel releases.
  - ArcReel releases trigger builds after successful image publication, avoiding released data before ready images.
  - Main previews likewise await releases, an accepted cost since their previous-version reporting already prevents enabling new models.
- Extend published endpoints in place when only adding legal values (such as `enum_maps` resolutions), without new endpoints/introduction versions. Versions exclude models uncallable on old instances; value extensions differ. Old implementations handle new choices until upgrades:
  - Declarative mapped parameters missing old `enum_maps` fail before submission with `ENUM_MAP_VALUE_MISSING`, without charges. Direct template parameters submit normally, upstream accepts/rejects, charging accepted requests.
  - Python legal-value tables ship with code. Old Vidu, for example, snaps unsupported durations to nearest legal values and submits normally with charges. In-place expansion does not change old behavior.
- Entries record release dates, shutdown dates, and vendor-official successors: vendor-level retirement applies to every provider, including custom ones. Evaluate shutdown by UTC date, retired from that day. Builds set `discontinued` when shutdown dates are no later than build day, enabling old date-unaware instances to recognize retirement.
- Do not immediately remove officially listed models retired by vendors: publish entries/list rows until 90 days after shutdown, then stop even if upstream retains them. Corrections of erroneous listings are exempt.
- Notify, never switch for users: references to retiring/retired models show dates/successors; users decide, without unknown cost/style changes.
- Synchronization never deletes entries; retain last versions removed upstream. Official built-in provider lists fully follow data; user additions remain until official data includes identical API names (`docs/adr/0099`).

## Considered Options

- **Instances reading third parties directly**: avoids repositories, but upstream format changes break all released clients together, mainland-China reachability is uncertain, and media sources remain missing.
- **Official-service distribution**: enables incremental/version-filtered data but disables sync with services and exposes instance sync behavior. Reuse `docs/adr/0078`'s zero-server/raw market route.
- **Fully manual ArcReel maintenance**: most control, but frequent text updates recreate stale-data problems.
- **Manual sync or confirmation after checks**: new-api does this, but users not clicking retain release-time data. Edited entries already automatically disable sync, protecting them from full automation.
- **Per-field preview selection (new-api)**: produces half-upstream/half-user entries, contradicting `docs/adr/0099` whole-copy rules.
- **Schema versions with entry validation**: rejects unreadable data, but unpriced fallbacks suffice for reference costs. Only unsupported new request encoding causes real invocation failures, handled by provider-model minimum versions without versioning whole datasets.
- **Manually specifying every minimum without endpoint derivation**: simpler format, but maintainers must remember endpoint releases and old name-based routing, inviting mistakes. Manual values after derivation cover nonendpoint causes only.
- **release-please `x-release-please-version` introduction annotations**: avoids manual arithmetic, but every release rewrites annotated lines instead of preserving first-release versions.
- **Expected next-release versions as introductions**: intuitive, but patch/minor is determined only after merge; overestimates hide models from their first supporting release.
- **No minimum versions, delaying incompatible catalog models instead**: simpler data, but early publication breaks old instances, late publication delays new ones. New defaults break every old instance lacking explicit defaults.
- **Publishing immediately on catalog merge**: earlier models, but latest-release instances cannot see models on unreleased endpoints; with version prompts, they are asked to upgrade to an unavailable version.
- **Substituting defaults while originals are held**: avoids build failures but changes instance defaults without consent.
- **Only currently usable models, removed on deprecation announcements**: tidy catalogs, but announcements commonly precede shutdown by 1–3 months. Early removal invalidates still-usable settings with uninformative "not listed" errors, hiding reasons/successors.
- **Never removing retired models**: perpetual explanations but unlimited catalog growth. Instances already retain latest received entries and can still explain retirement after retention ends.
- **Automatically using successors on calls**: avoids user action but silently changes prices/visual styles.
- **Channel-level lifecycles**: could express earlier provider-specific removals, but no real use case yet; add shapes only when needed.
- **Mechanical machine-readable-only inspections**: no Agents, but successors lack machine-readable sources and Chinese shutdowns appear only in announcements; Agents therefore inspect vendor-official pages.
- **Deleting upstream-removed entries on sync**: cleaner catalogs, but referencing provider models lose entries and all future calls become unpriced.

## Consequences

- Built-in prices/metadata change without code edits/releases.
- New models reusing request structures need only catalog data; new shapes await built-in-endpoint releases, preventing wrong old-client calls.
- Endpoint additions/changes must update `arcreel_catalog_core` catalogs or consistency tests fail. New endpoints require introduction versions; legal-value-only expansions neither add nor rewrite them.
- New-endpoint models publish only after ArcReel releases. Release-PR factory snapshots omit them; instances obtain them from post-release synchronization builds.
- Defaults requiring versions above latest release keep catalog publication failing until releases, delaying unrelated updates such as prices too.
- New maintenance: upstream format changes fail builds and require converter updates.
- Official services no longer need model-information endpoints.
