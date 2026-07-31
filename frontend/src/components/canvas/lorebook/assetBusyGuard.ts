import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, type ResourceKind } from "@/stores/tasks-store";

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
  if (!isResourceBusy(kind, projectName, name)) return false;
  useAppStore.getState().pushToast(t(hintKey), "info");
  return true;
}
