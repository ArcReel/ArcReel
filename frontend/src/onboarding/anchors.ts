/**
 * Onboarding anchor registry.
 *
 * Register names once, referenced by both ends: components use `data-onboarding={ONBOARDING_ANCHORS.x}`,
 * steps use `anchor: ONBOARDING_ANCHORS.x`. Name drift becomes typecheck errors instead of silently
 * missing highlights. When changing `data-onboarding` elements, recheck this table and corresponding step explanations.
 *
 * | Anchor | Step | Target |
 * |---|---|---|
 * | `lobby-create-project` | Lobby 2 | Main action of header New project split buttons |
 * | `lobby-settings` | Lobby 3 | Header settings icons, with warning dots for incomplete configuration |
 * | `settings-providers` | Settings 4 | Sidebar Providers entry |
 * | `settings-agent` | Settings 5 | Sidebar ArcReel Agent entry, never entire Agent groups |
 * | `lobby-demo-card` | Lobby 6 | Example-project cards during onboarding, bridging into demos |
 * | `workbench-agent` | Workbench 7 | Right Agent panels themselves, static demo conversations |
 * | `workbench-overview` | Workbench 8 | Overview story-setting sections |
 * | `workbench-lorebook` | Workbench 9 | Character-gallery grids |
 * | `workbench-timeline` | Workbench 10 | Episode storyboard bodies, routed with `?view=board` |
 * | `workbench-export` | Workbench 11 | Header Export buttons |
 *
 * Five workbench steps run in readonly demos (`demo-project.ts`). Overview/character/storyboard/export
 * anchors attach to real workbench components rather than demo copies: both share implementations,
 * so anchors work for both. `workbench-agent` differs: real panels write and never mount in demos;
 * anchor demo-only `DemoAssistantPanel` roots, filling Agent panels.
 *
 * Lobby welcome/closing steps use centered popovers without anchors.
 */

export const ONBOARDING_ANCHORS = {
  lobbyCreateProject: "lobby-create-project",
  lobbyDemoCard: "lobby-demo-card",
  lobbySettings: "lobby-settings",
  settingsProviders: "settings-providers",
  settingsAgent: "settings-agent",
  workbenchOverview: "workbench-overview",
  workbenchAgent: "workbench-agent",
  workbenchLorebook: "workbench-lorebook",
  workbenchTimeline: "workbench-timeline",
  workbenchExport: "workbench-export",
} as const;

export type OnboardingAnchor = (typeof ONBOARDING_ANCHORS)[keyof typeof ONBOARDING_ANCHORS];
