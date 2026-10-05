import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, selectActiveResourceIds, useActiveResourceIds, useTasksStore, type ResourceKind } from "@/stores/tasks-store";

/**
 * 角色写入可能搬动衍生文件，本体与衍生任务共同占用这个资产；
 * 反过来衍生（`本体名/衍生名`）也随本体一起占用，与衍生行的复核口径一致。
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

/** 响应式占用判定与提交时的新鲜读使用同一角色/衍生归属口径。 */
export function useAssetBusyNames(kind: ResourceKind, projectName: string): ReadonlySet<string> {
  const own = useActiveResourceIds(kind, projectName);
  const derivatives = useActiveResourceIds("character_derivative", kind === "character" ? projectName : null);
  return new Set([...own, ...[...derivatives].map((id) => id.split("/")[0])]);
}

/**
 * 占用感知型操作的提交时刻复核：从点击按钮到真正提交之间，该资源可能已被别处（另一标签页、
 * Agent、image_edit）入队占用，只查渲染时刻会留一个竞态窗口。命中则拒绝并给出可见反馈。
 *
 * `hintKey` 让调用方给出与自身动作匹配的提示文案，默认沿用立绘上传的措辞。
 *
 * `kind` 取 `taskResourceKind` 的资源种类口径，与 `StudioCanvasRouter` 算各卡片
 * `generating` 的口径同源，不另立判据。
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
