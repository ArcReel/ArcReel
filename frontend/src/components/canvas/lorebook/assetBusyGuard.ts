import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { selectActiveResourceIds, useActiveResourceIds, useTasksStore, type ResourceKind } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

/** 批量目标的一个占用槽：`kind` 与资源 id 由批量入队动作层的同口径给出。 */
export interface AssetBusySlot {
  kind: ResourceKind;
  id: string;
}

/**
 * 角色写入可能搬动衍生文件，本体与衍生任务共同占用这个资产；
 * 反过来衍生（`本体名/衍生名`）也随本体一起占用，与衍生行的复核口径一致。
 */
function busyIn(
  tasks: TaskItem[],
  optimisticActive: ReadonlySet<string>,
  kind: ResourceKind,
  projectName: string,
  name: string,
): boolean {
  if (selectActiveResourceIds(tasks, kind, projectName, optimisticActive).has(name)) return true;
  if (kind === "character_derivative") {
    const owner = name.split("/")[0];
    return owner !== name && busyIn(tasks, optimisticActive, "character", projectName, owner);
  }
  if (kind !== "character") return false;
  return [...selectActiveResourceIds(tasks, "character_derivative", projectName, optimisticActive)]
    .some((id) => id.startsWith(`${name}/`));
}

/** 单点占用判定：取 store 的当前快照，供提交那一刻的新鲜复核使用。 */
export function isAssetBusy(kind: ResourceKind, projectName: string, name: string): boolean {
  const { tasks, optimisticActive } = useTasksStore.getState();
  return busyIn(tasks, optimisticActive, kind, projectName, name);
}

/**
 * 响应式版本：任一目标命中自己的占用槽即 true。批量入口的目标集合由服务端决定，
 * 确认框据此在打开期间同步禁用提交；提交那一刻另用 {@link isAssetBusy} 新鲜复核，
 * 两者共用同一判据，不另立占用口径。
 */
export function useAssetSlotsBusy(projectName: string, slots: readonly AssetBusySlot[]): boolean {
  return useTasksStore((s) =>
    slots.some((slot) => busyIn(s.tasks, s.optimisticActive, slot.kind, projectName, slot.id)),
  );
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
