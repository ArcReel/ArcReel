# Advertising/short films do not use script_plan→prompt_authoring content confirmation

The ad (advertising/short-film) content type does not use the two-stage script_plan→prompt_authoring pipeline or blocking Web content confirmation. Confirmation applies to every structured drama / narration script_plan variant, including reference-to-video `script_plan_reference_units.json`; ad has no script_plan, so it does not apply.

## Why this is out of scope

The two core problems addressed by script_plan→prompt_authoring confirmation (docs/adr/0041) are structurally absent from ad:

- **Distortion during a second transcription**: verbatim drama / narration speech crosses two generation stages, risking rewrites between them. ad generates its final structure in one call (flat `shots[]` + first-class spoken copy `voiceover_text`), without a two-stage transcription boundary.
- **Unreviewable intermediate state**: Web users cannot see drama / narration after script_plan finishes but before prompt_authoring runs. ad writes directly to `scripts/episode_1.json`, visible and editable in the Web timeline throughout.

The partly applicable concern, no hard checkpoint before expensive visuals, is covered in two ways: ad is always one episode with few shots (15–90-second final cuts), limiting cost exposure compared with a drama episode; the Agent workflow already requires stage confirmations, presenting shots/spoken copy after script generation, a soft `product_sheet` gate, and product-fidelity review after storyboard generation before video costs.

Generating ad scripts in one step without intermediate script_plan is intentional in ADR 0033, recorded by GLOSSARY.md’s advertising/short-film mode (ad) entry. Building a separate two-stage ad pipeline merely for gate symmetry does not offer benefits proportionate to its cost.

## Revisit conditions

- Real ad fidelity problems: LLMs rewrite verbatim brief copy and users cannot trace where it changed;
- ad script generation evolves into two stages for another reason; connect the gate then (the unified filename registry requires one registration, see #985).

## Prior requests

- #987 — 广告/短片未接入 script_plan→prompt_authoring 内容确认（契约空白） (ad does not use script_plan→prompt_authoring confirmation, leaving a contract gap)
