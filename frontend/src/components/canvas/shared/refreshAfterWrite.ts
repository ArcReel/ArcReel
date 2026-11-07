import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore, type RefreshProjectResult } from "@/stores/projects-store";

/**
 * Refresh project data after successful writes, whether immediate actions or editing-unit saves.
 *
 * The write has committed; refresh is separate, and `refreshProject` reports failure through its settled value rather than rejection.
 * Without feedback, stale UI looks like an ineffective write and invites repeated actions.
 * `cancelled` means the project changed; remain silent. Return settlement unchanged so callers judge whether next actions remain reliable.
 *
 * Writes with dedicated feedback may call `refreshProject` directly and handle settlement themselves:
 * storyboard saves via `PartialSaveError`, and timeline shot additions/deletions, renames, merges and deletion.
 */
export async function refreshAfterWrite(
  projectName: string,
  t: TFunction,
  options?: { invalidateKeys?: string[] },
): Promise<RefreshProjectResult> {
  const result = await useProjectsStore.getState().refreshProject(projectName, options);
  if (result === "failed") useAppStore.getState().pushToast(t("common:write_refresh_failed"), "warning");
  return result;
}
