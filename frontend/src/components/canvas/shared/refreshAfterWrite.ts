import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore, type ProjectRefreshScope, type RefreshProjectResult } from "@/stores/projects-store";

export async function refreshAfterWrite(
  projectName: string,
  t: TFunction,
  options?: { invalidateKeys?: string[]; scope?: ProjectRefreshScope },
): Promise<RefreshProjectResult> {
  const result = await useProjectsStore.getState().refreshProject(projectName, options);
  if (result === "failed") useAppStore.getState().pushToast(t("common:write_refresh_failed"), "warning");
  return result;
}
