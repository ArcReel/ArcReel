# arcreel-market-core

ArcReel's market-source tools and endpoint-definition validator. The ArcReel application, market-source repository CI, and official services share this implementation, giving the same definition the same validation result in all three places.

## Relationship to `arcreel-market`

[`ArcReel/arcreel-market`](https://github.com/ArcReel/arcreel-market) is the official market-source repository. Entries follow the `endpoints/<slug>/definition.json` directory convention (plus optional `icon.<png|webp|svg>`); root `arcreel-market.json` is the index generated from those directories. This package handles:

- Generating indexes from directories (`generate`);
- Validating market-source directories against all rules (`check`): index structure, slugs, sidecar files and icons, entry definitions, index-entry consistency with the definition `meta` projection, and version-threshold format;
- Market-source address parsing, index parsing, and remote-fetch boundaries for clients.

Market-source repositories maintain no validation logic. Their PR validation and index publishing invoke two reusable workflows from the ArcReel repository (`market-validate.yml`, `market-publish-index.yml`). The workflows install this package, inject application translations through the main repository's `scripts.market`, and run the two commands above. Third-party market sources (forked or self-built) integrate the same way.

## Why include leaf modules such as video backend contracts

The endpoint-definition validator has two layers: `schema.json` governs structure; semantic validation checks placeholders, extraction paths, state mappings, and capability declarations. The semantic layer depends on several market-independent leaf modules, included in this package:

| Module | Validator use |
|---|---|
| `video_backend_contract` | Four-category provider-job status normalization, reference-audio modes, and their consistency with counts |
| `aspect_size` | Template-engine dimension derivation from aspect ratio and resolution tier |
| `auth_section` | Shared `auth` template primitives and checks for both `kind` values |
| `definition_diagnostics`, `definition_schema_errors` | Diagnostic carriers and normalization of jsonschema errors |
| `comfyui` | Structure contracts and semantic validation for `kind: comfyui` definitions |
| `validation_messages` | Locale-neutral message carriers |

These modules depend only on the standard library and this package's third-party dependencies, without depending on any part of the ArcReel application. The application imports them directly at runtime rather than keeping copies.

## Diagnostics and translation

Diagnostics always consist of stable codes and parameters (`ValidationMessage(key, params)`); this package carries no translation catalogs. Callers supply a translator for rendering. Boundaries without translation catalogs (this package's CLI and exception text) use `code_translator` to output keys and parameters themselves. All emitted message keys are listed in `arcreel_market_core.message_keys.MESSAGE_KEYS`. ArcReel's zh / en / vi catalogs cover every key, protected by application-side consistency tests.

## Usage

Command line:

```bash
python -m arcreel_market_core generate <market-source-directory> [--name ...] [--default-name ...] [--dry-run]
python -m arcreel_market_core check <market-source-directory>
```

A nonzero exit code means failure. Diagnostics appear one per line as `file:location: [code] key(params)`; summaries also output keys and parameters. For natural-language diagnostics inside the ArcReel repository, use the same entry point as the workflows:

```bash
python -m scripts.market [--locale zh|en|vi] check <market-source-directory>
python -m scripts.market [--locale zh|en|vi] generate <market-source-directory>
```

Chinese is the default; `arcreel_market_core.market.cli.main` also accepts `translate=` for the host to inject its own translator.

Install as a dependency pinned to a Git tag:

```bash
pip install "arcreel-market-core @ git+https://github.com/ArcReel/ArcReel@<tag>#subdirectory=packages/arcreel-market-core"
```

```python
from arcreel_market_core.endpoint_definition import validate_definition
from arcreel_market_core.market import check_source

diagnostics = validate_definition(definition)
payload = diagnostics.to_payload(translate)  # {"errors": [{"path", "code", "message"}], "warnings": [...]}
```

## Development

This package belongs to the ArcReel repository's uv workspace. Tests live in this directory's `tests/` and run separately from application tests:

```bash
uv run python -m pytest packages/arcreel-market-core/tests
```
