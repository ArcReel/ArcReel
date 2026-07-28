import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy } from "@/stores/tasks-store";

/**
 * 立绘上传的提交时刻复核：从点击上传按钮到文件选完之间，该资源可能已被别处（另一标签页、
 * Agent、image_edit）入队占用，只查渲染时刻会留一个竞态窗口。命中则拒绝并给出可见反馈。
 *
 * `kind` 取 `isResourceBusy` 的资源种类口径，与 `StudioCanvasRouter` 算各卡片
 * `generating` 的口径同源，不另立判据。
 */
export function rejectIfAssetBusy(
  kind: string,
  projectName: string,
  name: string,
  t: TFunction,
): boolean {
  if (!isResourceBusy(kind, projectName, name)) return false;
  useAppStore.getState().pushToast(t("assets:upload_sheet_busy_hint"), "info");
  return true;
}
