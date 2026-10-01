import { ROUTE_APP_SETTINGS } from "@/app-routes";
import type { TaskItem } from "@/types";

const CUSTOM_PROVIDER_PREFIX = "custom-";

/** 文本任务因输出被截断而失败时的模型；`custom` 为自定义供应商的模型，可以去设置里登记最大输出长度。 */
export interface OutputTruncation {
  providerId: string;
  model: string;
  custom: boolean;
}

/** 失败任务的输出截断信息：问题码为 `text_output_truncated` 且带出模型时返回，否则返回 null。 */
export function outputTruncationOf(task: Pick<TaskItem, "error_code" | "error_params">): OutputTruncation | null {
  if (task.error_code !== "text_output_truncated") return null;
  const params = task.error_params ?? {};
  const providerId = params.provider_id;
  const model = params.model;
  if (typeof providerId !== "string" || typeof model !== "string") return null;
  return { providerId, model, custom: params.custom_model === true };
}

/** 自定义供应商模型在设置页的位置：打开这个供应商的编辑表单并定位到这个模型。内置供应商返回 null。 */
export function customModelSettingsPath(providerId: string, model: string): string | null {
  if (!providerId.startsWith(CUSTOM_PROVIDER_PREFIX)) return null;
  const id = providerId.slice(CUSTOM_PROVIDER_PREFIX.length);
  if (!/^\d+$/.test(id)) return null;
  const params = new URLSearchParams({ section: "providers", custom: id, model });
  return `${ROUTE_APP_SETTINGS}?${params.toString()}`;
}
