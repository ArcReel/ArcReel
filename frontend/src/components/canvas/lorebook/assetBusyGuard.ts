import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, selectActiveResourceIds, useActiveResourceIds, useTasksStore, type ResourceKind } from "@/stores/tasks-store";

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

export function useAssetBusyNames(kind: ResourceKind, projectName: string): ReadonlySet<string> {
  const own = useActiveResourceIds(kind, projectName);
  const derivatives = useActiveResourceIds("character_derivative", kind === "character" ? projectName : null);
  return new Set([...own, ...[...derivatives].map((id) => id.split("/")[0])]);
}

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
