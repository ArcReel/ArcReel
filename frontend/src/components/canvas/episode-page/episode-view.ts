/**
 * Episode view routing: URL `?view=` stores the current view; define all values/availability only here.
 *
 * | Value | View | Availability |
 * | --- | --- | --- |
 * | `setting` | Story settings (ad synopsis, genre, theme, world) | Ads only, first tab |
 * | `plan` | Script planning (content confirmation; source confirmation before scripts) | Non-ads with intermediate plans, pending source confirmation, or reference-to-video |
 * | `grid` | Grid storyboards | Storyboard-image-to-video with grids enabled; unavailable during source confirmation |
 * | `board` | Storyboards (video units for reference-to-video) | Always present; unavailable with plans but no formal script |
 * | `edit` | Editing | Non-demo projects with formal scripts |
 *
 * Default is `board` with formal scripts, otherwise `plan` with script plans. Write
 * `view` only for nondefault selections, so pages on defaults automatically reach storyboards after generation. Invalid/unavailable values display defaults.
 */
import { EPISODE_VIEW_EDIT, EPISODE_VIEW_PARAM } from "@/app-routes";
import type { EpisodeMeta, ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";
import type { GenerationRoute } from "@/utils/generation-mode";

export const EPISODE_VIEWS = ["setting", "plan", "grid", "board", EPISODE_VIEW_EDIT] as const;
export type EpisodeView = (typeof EPISODE_VIEWS)[number];
/** Canvas-owned views; story settings/editing replace the whole canvas. */
export type CanvasView = Exclude<EpisodeView, "setting" | "edit">;

/** Facts determining episode views, computed by routing from project/episode state. */
export interface EpisodeViewFacts {
  isAd: boolean;
  route: GenerationRoute;
  /** Storyboard-image-to-video with grid storyboards enabled. */
  grid: boolean;
  /** Formal script exists. */
  hasScript: boolean;
  /** Script-plan intermediate exists (`script_status` segmented or generated). */
  hasDraft: boolean;
  /** Neither script nor intermediate exists; planning displays source confirmation. */
  sourceReview: boolean;
  /** Demo project: no editing view. */
  demo: boolean;
}

/** View-tab element id supplying its view region's accessible name. */
export function episodeViewTabId(view: EpisodeView): string {
  return `episode-view-tab-${view}`;
}

/** Intermediate exists: segmented draft or generated-script draft. */
export function hasEpisodeDraft(meta: EpisodeMeta | undefined): boolean {
  return meta?.script_status === "segmented" || meta?.script_status === "generated";
}

/**
 * Planning view shows source when an episode is selected without formal script/intermediate.
 * Ad/short films are single-episode; demos have no sources to split. Both use their own canvases.
 */
export function showsEpisodeSource(
  projectData: ProjectData | null,
  episode: number,
  script: EpisodeScript | null,
  demo: boolean,
): boolean {
  const meta = projectData?.episodes?.find((entry) => entry.episode === episode);
  return meta !== undefined && !script && !hasEpisodeDraft(meta) && projectData?.content_mode !== "ad" && !demo;
}

export interface EpisodeViewTab {
  view: EpisodeView;
  disabled: boolean;
}

function planAvailable(facts: EpisodeViewFacts): boolean {
  if (facts.isAd) return false;
  return facts.sourceReview || facts.hasDraft || (facts.route === "reference_video" && facts.hasScript);
}

/** Episode-header view tabs in display order. */
export function episodeViewTabs(facts: EpisodeViewFacts): EpisodeViewTab[] {
  const plan = planAvailable(facts);
  const tabs: EpisodeViewTab[] = [];
  if (facts.isAd) tabs.push({ view: "setting", disabled: false });
  if (plan) tabs.push({ view: "plan", disabled: false });
  if (facts.grid) tabs.push({ view: "grid", disabled: facts.sourceReview });
  // Reference-to-video unit lists are viewable at intermediate stage; storyboard-image-to-video waits for formal scripts. Without plans, storyboards are the sole view.
  const boardEnabled =
    facts.hasScript || !plan || (facts.route === "reference_video" && !facts.sourceReview);
  tabs.push({ view: "board", disabled: !boardEnabled });
  if (facts.hasScript && !facts.demo) tabs.push({ view: EPISODE_VIEW_EDIT, disabled: false });
  return tabs;
}

export function defaultEpisodeView(facts: EpisodeViewFacts): EpisodeView {
  if (facts.hasScript) return "board";
  return planAvailable(facts) ? "plan" : "board";
}

/** Resolve URL values to views, falling back to default for invalid/unavailable values. */
export function resolveEpisodeView(requested: string | null, facts: EpisodeViewFacts): EpisodeView {
  const tab = episodeViewTabs(facts).find((entry) => entry.view === requested);
  return tab && !tab.disabled ? tab.view : defaultEpisodeView(facts);
}

/**
 * Query parameters after switching to `next`: omit `view` for defaults, preserving all other parameters. Return a new object without mutating inputs;
 * leave-guard rejection must not already change current URL parameters.
 */
export function withEpisodeView(params: URLSearchParams, next: EpisodeView, facts: EpisodeViewFacts): URLSearchParams {
  const result = new URLSearchParams(params);
  if (next === defaultEpisodeView(facts)) result.delete(EPISODE_VIEW_PARAM);
  else result.set(EPISODE_VIEW_PARAM, next);
  return result;
}

/**
 * Whether in-app `to` navigation stays in the current view: same episode and URL `view` resolves to the same view above.
 *
 * Switching planning/grids/storyboards retains the canvas instance, but editing units mount only under one view
 * (shot details/unit bodies only in storyboards). Retained canvas does not imply retained editing units. They use this as
 * `allowNavigation`, bypassing questions only for navigation that retains themselves; other views, editing, or episodes still guard.
 * `to` and `here` include nested-routing bases; omitted `view` uses defaults computed from `facts`.
 */
export function staysInEpisodeView(to: string, here: string, facts: EpisodeViewFacts): boolean {
  const current = new URL(here, "http://episode-view.invalid");
  const target = new URL(to, current);
  if (target.pathname !== current.pathname) return false;
  return (
    resolveEpisodeView(target.searchParams.get(EPISODE_VIEW_PARAM), facts) ===
    resolveEpisodeView(current.searchParams.get(EPISODE_VIEW_PARAM), facts)
  );
}
