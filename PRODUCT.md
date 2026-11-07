# Product

## Platform

web

## Users

The primary user is a self-hosted individual creator: they deploy the service and independently take novels, finished scripts, or product materials through to final video. Interface decisions default to this user.

Leave room for two known expansion directions:

- **Scale**: collaboration and role-based permissions are not currently offered; structure the product for the possibility of more than one user.
- **Distribution**: brand names are dynamically configurable; the interface supports desktop-client packaging with the same design language.

## Product Purpose

Organize content analysis, asset management, storyboarding, media generation, cost tracking, and export into a reviewable video-production pipeline that can resume after interruption. Success means one creator independently completes the full path from source files to a final cut or Jianying draft, with visible progress, editable outputs, and actual spending at every stage.

## Positioning

Three differentiating mechanisms, in priority order:

1. **Agent orchestration throughout**: every step from script planning, assets, storyboards, and generation to final output can be orchestrated by the Agent, combining low onboarding effort with substantial automation.
2. **Open provider integration**: users choose providers/models for text, images, video, and TTS; built-in and custom providers coexist.
3. **Open-source self-hosting and Jianying delivery**: data stays on users’ machines, with Jianying-draft export for further manual refinement.

## Operating Context

- Prerequisites: project creation requires no configuration. Before production, configure the Agent and the text/image/video/TTS capabilities actually used in Settings.
- Production path: source files or product materials → content analysis/project planning → character, scene, prop, product assets → episodes/structured scripts → storyboard images or video units → video clips/narration tracks → final composition or Jianying-draft export. At every step the Agent may orchestrate and creators may review, adjust, or redo individual outputs.
- Deployment is a self-hosted service with one administrator account.
- Export targets mainland-China Jianying; CapCut compatibility is unverified. UI wording states Jianying-only support.

## Capabilities and Constraints

- Content types: narration/commentary, drama, advertising/short films. Generation modes: storyboard-to-video and reference-to-video. Generation modes are fixed after project creation.
- Generation tasks can be cancelled. Recovery after service interruption is conditional: only video tasks with provider progress-query support and recorded provider task IDs resume automatically. Images, dubbing, and other video tasks fail and await manual retry because resubmission bills again. The interface must continually show work in progress, blockers, and these failures requiring manual retry.
- Batch video generation has one admission assessment with three outcomes: allowed, confirmation required, or blocked. Batch images and dubbing do not have this assessment.
- `GLOSSARY.md` and `docs/adr/` fully define terminology, state machines, and artifact semantics; follow them when these concepts are involved.

## Brand Commitments

- Name: ArcReel. Default tagline: “Agent 驱动的 AI 视频创作工作台” (“Agent-driven AI video creation workbench”, `BRAND.tagline` default).
- **Dynamically configurable brand name**: name/tagline come from `BRAND` in `frontend/src/branding.ts` and i18n `[[brand]]` placeholders, overridable at build time.
- **Glossary governs interface terminology**: follow `GLOSSARY.md`; Chinese-copy standards are in `docs/standards/copy.md`.

## Evidence on Hand

- Product documentation `website/` (docs.arc-reel.com): getting started, creative workflows, provider configuration, Jianying export, FAQ, deployment/operations, architecture.
- Screenshots in `docs/assets/screenshots/`; style thumbnails in `frontend/public/style-thumbnails`; brand icons and manifest in `frontend/public/`.

## Product Principles

1. **Review before automation**: automated outputs are visible, editable, and individually redoable by default.
2. **Cost before execution**: before submitting credit-consuming operations, present scope and estimated cost, especially for batches.
3. **Server decisions define state**: progress, blockers, and artifacts use authoritative server judgments; the interface does not infer state from file existence.
4. **One person can complete it**: every critical path is independently usable by one person.

## Accessibility & Inclusion

- Complete multilingual synchronization is mandatory: user-facing copy needs translation keys in every supported language; layouts reserve room for generally longer translated text.
- No specific compliance-level target, such as a WCAG level, is established yet.
