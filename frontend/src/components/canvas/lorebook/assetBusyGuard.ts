import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { selectActiveResourceIds, useTasksStore, type ResourceKind } from "@/stores/tasks-store";
import type { AssetKind } from "@/types/reference-video";
import { normalizeAssetName } from "@/utils/reference-mentions";

type AssetDimension = "image" | "derivative_image" | "voice" | "whole_asset";
type TaskState = ReturnType<typeof useTasksStore.getState>;

function derivativeParts(id: string) {
  const [owner, derivative = ""] = id.split("/");
  return [normalizeAssetName(owner), normalizeAssetName(derivative)] as const;
}

export function hasActiveAssetName(activeIds: ReadonlySet<string> | undefined, name: string): boolean {
  if (!activeIds || activeIds.size === 0) return false;
  if (activeIds.has(name)) return true;
  const target = normalizeAssetName(name);
  for (const id of activeIds) {
    if (normalizeAssetName(id) === target) return true;
  }
  return false;
}

export function hasActiveDerivativeId(activeIds: ReadonlySet<string> | undefined, resourceId: string): boolean {
  if (!activeIds || activeIds.size === 0) return false;
  if (activeIds.has(resourceId)) return true;
  const [owner, derivative] = derivativeParts(resourceId);
  for (const id of activeIds) {
    const [activeOwner, activeDerivative] = derivativeParts(id);
    if (activeOwner === owner && activeDerivative === derivative) return true;
  }
  return false;
}

function selectAssetBusy(
  state: TaskState,
  dimension: AssetDimension,
  kind: AssetKind,
  projectName: string,
  name: string,
): boolean {
  const [owner, derivative] = dimension === "derivative_image"
    ? derivativeParts(name)
    : [normalizeAssetName(name), ""];
  const hasOwner = (slot: ResourceKind) =>
    [...selectActiveResourceIds(state, slot, projectName)].some((id) => normalizeAssetName(id) === owner);

  if (dimension === "voice") return kind === "character" && hasOwner("voice_sample");
  if (hasOwner(kind)) return true;
  if (kind !== "character") return false;
  if (dimension === "whole_asset" && hasOwner("voice_sample")) return true;

  return [...selectActiveResourceIds(state, "character_derivative", projectName)].some((id) => {
    const [activeOwner, activeDerivative] = derivativeParts(id);
    return activeOwner === owner && (dimension !== "derivative_image" || activeDerivative === derivative);
  });
}

export function isAssetBusy(dimension: AssetDimension, kind: AssetKind, projectName: string, name: string): boolean {
  return selectAssetBusy(useTasksStore.getState(), dimension, kind, projectName, name);
}

export function useAssetBusy(
  dimension: AssetDimension,
  kind: AssetKind | undefined,
  projectName: string,
  name: string | undefined,
): boolean {
  return useTasksStore((state) =>
    kind !== undefined && name !== undefined && selectAssetBusy(state, dimension, kind, projectName, name),
  );
}

export function rejectIfAssetBusy(
  dimension: AssetDimension,
  kind: AssetKind,
  projectName: string,
  name: string,
  t: TFunction,
  hintKey = "assets:upload_sheet_busy_hint",
): boolean {
  if (!isAssetBusy(dimension, kind, projectName, name)) return false;
  useAppStore.getState().pushToast(t(hintKey), "info");
  return true;
}
