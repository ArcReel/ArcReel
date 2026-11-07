import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, selectActiveResourceIds, useActiveResourceIds, useTasksStore, type ResourceKind } from "@/stores/tasks-store";

/**
 * Character writes may move derivative files; base and derivative tasks jointly occupy the asset.
 * Conversely derivatives (`本体名/衍生名`) occupy it with their base, matching derivative-row rechecks.
 */
export function isAssetBusy(kind: ResourceKind, projectName: string, name: string): boolean {
  if (isResourceBusy(kind, projectName, name)) return true;
  if (kind === "character_derivative") {
    const owner = name.split("/")[0];
    return owner !== name && isAssetBusy("character", projectName, owner);
  }
  if (kind !== "character") return false;
  const { tasks, optimisticActive } = useTasksStore.getState();
  return [...selectActiveResourceIds(tasks, "character_derivative", projectName, optimisticActive)]
    .some((id) => id.startsWith(`${name}/`));
}

/** Reactive occupancy checks and fresh submit-time reads share character/derivative ownership rules. */
export function useAssetBusyNames(kind: ResourceKind, projectName: string): ReadonlySet<string> {
  const own = useActiveResourceIds(kind, projectName);
  const derivatives = useActiveResourceIds("character_derivative", kind === "character" ? projectName : null);
  return new Set([...own, ...[...derivatives].map((id) => id.split("/")[0])]);
}

/**
 * Submit-time recheck for occupancy-aware actions: between button click and submission, another tab,
 * Agent, or image_edit may queue work for this resource. Render-time checks alone leave a race; reject matches with visible feedback.
 *
 * `hintKey` lets callers choose action-specific feedback, defaulting to character-sheet upload wording.
 *
 * `kind` uses `taskResourceKind` resource rules, shared with `StudioCanvasRouter` card
 * `generating` calculations rather than separate criteria.
 */
export function rejectIfAssetBusy(
  kind: ResourceKind,
  projectName: string,
  name: string,
  t: TFunction,
  hintKey = "assets:upload_sheet_busy_hint",
): boolean {
  if (!isAssetBusy(kind, projectName, name)) return false;
  useAppStore.getState().pushToast(t(hintKey), "info");
  return true;
}
