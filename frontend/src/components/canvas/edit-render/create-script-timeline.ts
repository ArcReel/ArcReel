import type { TFunction } from "i18next";
import { API } from "@/api";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import type { RefreshProjectResult } from "@/stores/projects-store";
import type { EditTimelineReadout } from "@/types/edit-timeline";

/** Display name for a timeline created from the script: Full version, adding successive numbers if the episode already has that name. */
function nextEditTimelineName(base: string, numbered: (n: number) => string, taken: string[]): string {
  const names = new Set(taken.map((name) => name.toLocaleLowerCase()));
  if (!names.has(base.toLocaleLowerCase())) return base;
  for (let n = 2; ; n += 1) {
    const candidate = numbered(n);
    if (!names.has(candidate.toLocaleLowerCase())) return candidate;
  }
}

/**
 * Mechanically create an editing timeline from the current script, using whole clips and hard cuts without the Agent, then refresh the project.
 * Production progress and editing views reread it after refresh. Both the WorkflowPanel Editing row and empty editing view use this action.
 * Refresh failures already show feedback; `refreshed` lets callers decide whether to report success too.
 */
export async function createScriptEditTimeline(
  projectName: string,
  episode: number,
  t: TFunction,
): Promise<{ created: EditTimelineReadout; refreshed: RefreshProjectResult }> {
  const { timelines } = await API.listEditTimelines(projectName, episode);
  const name = nextEditTimelineName(
    t("workflow:edit_timeline_default_name"),
    (number) => t("workflow:edit_timeline_default_name_numbered", { number }),
    timelines.map((timeline) => timeline.name),
  );
  const created = await API.createEditTimeline(projectName, episode, name);
  return { created, refreshed: await refreshAfterWrite(projectName, t) };
}
