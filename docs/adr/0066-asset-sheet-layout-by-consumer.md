---
status: accepted
---

# Asset-sheet layout follows downstream consumption; characters retain three views

An asset sheet is primarily a **conditioning input**: an i2i reference for storyboard generation, a grid reference, or the image directly driving reference-to-video generation. Layout follows that purpose: scenes, props, and products each use **a single image**; characters use **front / profile / back views arranged horizontally**. Human viewing/confirmation on asset cards is secondary and does not determine layout.

**Single images are the evidence-supported shape.** Repository research (`docs/research/arcreel-asset-sheet-prompt-research.md`) checked eight image providers' official guides page by page; none recommends arranging multiple views of one subject into one image. Google's dedicated 360-view section generates each angle separately, feeding generated images back as references; Black Forest Labs' comic-panel section generates panels individually; Seedream directs design sets toward multi-image output. Official consistency paths are only multi-reference channels or grouped multi-image generation. Mechanistic paper evidence also exists: references not bound to textual concepts cause copy-paste effects, with attention maps failing to focus on target subjects (arXiv:2503.06568, tested on open-source adapter architectures; application to closed commercial APIs is inference).

**Character three-view sheets contradict this evidence and instead follow common practice in creative products** (three views for characters, single views for props/scenes), not primary evidence. Accept the resulting costs: multi-panel layouts may continue propagating downstream through i2i / r2v for characters; reference-to-video models limited to one reference receive one three-panel collage. Do not compensate by cropping a main area or selecting panels: those layers assume "the layout is wrong," whereas this layout is intentional.

**Keep guard sentences only as positive exclusion statements.** Scenes state that no people appear; products state fidelity to reference appearance and display only the product itself. These statements match Google and Black Forest Labs' official guidance to "describe what you want, not what you do not want." Several papers likewise support removing identity-irrelevant reference content (Subject-Diffusion's segmentation ablation, PhotoMaker's noise outside human regions, PuLID treating background/composition as ID-irrelevant); for scenes, people rather than backgrounds are irrelevant. Do not put anatomical safeguards (facial symmetry, finger counts, limb proportions) or quality words (complete structure, clear focus, normal perspective) into prompts: none of the eight official guides endorses them, and quality words dilute CFG weights. Retain cross-panel character consistency requirements: these constrain relations among three panels within one image, not anatomical quality.

**Retain solid backgrounds**: pure white for characters, clean light gray for props/products. Not applicable to scenes, whose environment is the subject itself.

**Keep negative exclusions textual, defined per image type and appended to prompts.** Official guidance differs by target model: OpenAI explicitly encourages `no watermark / no extra text / no logos` in body text; Google and Black Forest Labs explicitly require positive rewrites, and FLUX.2 does not support negative prompts. Repository prompt construction is backend-independent, so identical suffixes reach models with opposing guidance. Papers report low compliance with textual negation (12.3% on DALL·E 3 in arXiv:2411.17066; CLIP-family negation comprehension near random in arXiv:2501.09425), but no primary source shows writing it is worse than omitting it, so retain it.

**Asset-image generation must explicitly specify aspect ratio.** Google's official guidance says unspecified output dimensions follow input images, otherwise defaulting to 1:1. Any generation path omitting the ratio lets the asset image's ratio replace project settings as the downstream output-ratio source.

## Explicitly rejected

- **Multiple images per asset** (separate angle images sent through providers' multi-reference channels): papers support multiple separate references over one (DreamBooth's 1→5-image ablation, PhotoMaker's inference-time benefit, SynCD's 1→3-image improvement), but this changes sheet fields from one path to multiple paths, affecting data validation, asset-rename cascades, version history, and frontend cards. This is an asset-model issue, not a layout issue.
- **Project- or style-specific layout overrides**: keep prompt construction as one source of truth with zero configuration dimensions.
- **Cropping a main region by layout before reference submission or dynamically selecting panels by model reference limits**: as above, the compensation layer's premise does not hold.
- **A negative_prompt parameter channel**: that channel has independent mechanisms characterized by papers (Delayed Effect and Deletion Through Neutralization in arXiv:2406.02965); abandoning it is an existing choice, independent of this layout decision.
- **Unsourced quantities and purpose statements**: panel-width percentages, frame-area proportions, background-color hex values, and angle-specific purposes such as "show depth." Do not replace removed material with another set of unsourced values or claims.

## Consequences

- **Shot size, angle, pose, and aspect ratio lack primary evidence**; current values are unvalidated defaults. All four asset-image types use 16:9: horizontal character layout follows three-view sheets, while the other three types inherit it without a layout rationale.
- **No automated defense protects layout changes**: do not write tests asserting template text; template drift does not alert. Acceptance uses manual QA: generate one image per asset under old/new templates, run downstream i2i for each, and compare collage inheritance and identity fidelity.
- Scene asset images no longer contain embedded smaller images outside the main view, eliminating the composition contamination they propagate into storyboards through i2i.
