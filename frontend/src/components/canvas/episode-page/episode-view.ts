import { hasEpisodeDraft } from "@/utils/episode-display";
import { EPISODE_VIEW_EDIT, EPISODE_VIEW_PARAM } from "@/app-routes";
import type { EpisodeMeta, ProjectData } from "@/types";
import type { EpisodeScript } from "@/types/script";
import type { GenerationRoute } from "@/utils/generation-mode";

export const EPISODE_VIEWS = ["setting", "plan", "grid", "board", EPISODE_VIEW_EDIT] as const;
export type EpisodeView = (typeof EPISODE_VIEWS)[number];
export type CanvasView = Exclude<EpisodeView, "setting" | "edit">;

export interface EpisodeViewFacts {
  isAd: boolean;
  route: GenerationRoute;
  grid: boolean;
  hasScript: boolean;
  hasDraft: boolean;
  sourceReview: boolean;
  demo: boolean;
}

export function episodeViewTabId(view: EpisodeView): string {
  return `episode-view-tab-${view}`;
}

export function showsEpisodeSource(
  projectData: ProjectData | null,
  episode: number,
  script: EpisodeScript | null,
  demo: boolean,
): boolean {
  const meta = projectData?.episodes?.find((entry) => entry.episode === episode);
  return meta !== undefined && !script && !hasEpisodeDraft(meta) && projectData?.content_mode !== "ad" && !demo;
}

export function episodeSourceOrigin(meta: EpisodeMeta | undefined): NonNullable<EpisodeMeta["source_origin"]> {
  return meta?.source_origin ?? (meta?.source_range ? "whole_source" : "none");
}

export function canViewEpisodeSource(
  projectData: ProjectData | null,
  episode: number,
  script: EpisodeScript | null,
  demo: boolean,
): boolean {
  const meta = projectData?.episodes?.find((entry) => entry.episode === episode);
  return meta !== undefined && Boolean(script) && episodeSourceOrigin(meta) !== "none" && projectData?.content_mode !== "ad" && !demo;
}

export interface EpisodeViewTab {
  view: EpisodeView;
  disabled: boolean;
}

function planAvailable(facts: EpisodeViewFacts): boolean {
  if (facts.isAd) return false;
  return facts.sourceReview || facts.hasDraft || (facts.route === "reference_video" && facts.hasScript);
}

export function episodeViewTabs(facts: EpisodeViewFacts): EpisodeViewTab[] {
  const plan = planAvailable(facts);
  const tabs: EpisodeViewTab[] = [];
  if (facts.isAd) tabs.push({ view: "setting", disabled: false });
  if (plan) tabs.push({ view: "plan", disabled: false });
  if (facts.grid) tabs.push({ view: "grid", disabled: facts.sourceReview });
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

export function resolveEpisodeView(requested: string | null, facts: EpisodeViewFacts): EpisodeView {
  const tab = episodeViewTabs(facts).find((entry) => entry.view === requested);
  return tab && !tab.disabled ? tab.view : defaultEpisodeView(facts);
}

export function withEpisodeView(params: URLSearchParams, next: EpisodeView, facts: EpisodeViewFacts): URLSearchParams {
  const result = new URLSearchParams(params);
  if (next === defaultEpisodeView(facts)) result.delete(EPISODE_VIEW_PARAM);
  else result.set(EPISODE_VIEW_PARAM, next);
  return result;
}

export function staysInEpisodeView(to: string, here: string, facts: EpisodeViewFacts): boolean {
  const current = new URL(here, "http://episode-view.invalid");
  const target = new URL(to, current);
  if (target.pathname !== current.pathname) return false;
  return (
    resolveEpisodeView(target.searchParams.get(EPISODE_VIEW_PARAM), facts) ===
    resolveEpisodeView(current.searchParams.get(EPISODE_VIEW_PARAM), facts)
  );
}
