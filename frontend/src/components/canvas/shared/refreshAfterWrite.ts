import type { TFunction } from "i18next";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore, type RefreshProjectResult } from "@/stores/projects-store";

/**
 * 写入（即时动作与编辑单元的保存）成功后刷新项目数据。
 *
 * 写入已提交，刷新是独立的后续步骤：`refreshProject` 以结算值报告失败而不 reject。
 * 不提示的话界面停在旧数据上，看着像这次写入没生效，创作者容易重复操作。
 * `cancelled` 是项目已切走，静默。结算值原样返回，调用方据此决定后续动作是否仍可靠。
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
