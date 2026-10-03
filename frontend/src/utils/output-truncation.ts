import { ApiRequestError } from "@/api/errors";
import { settingsSectionPath } from "@/app-routes";
import type { TaskItem } from "@/types";

const CUSTOM_PROVIDER_PREFIX = "custom-";

/** 文本任务因输出被截断而失败时的模型；`custom` 为自定义供应商的模型，可以去设置里登记最大输出长度。 */
export interface OutputTruncation {
  providerId: string;
  model: string;
  custom: boolean;
}

function truncationFromProblem(code: unknown, params: Record<string, unknown>): OutputTruncation | null {
  if (code !== "text_output_truncated") return null;
  const providerId = params.provider_id;
  const model = params.model;
  if (typeof providerId !== "string" || typeof model !== "string") return null;
  return { providerId, model, custom: params.custom_model === true };
}

/** 失败任务的输出截断信息：问题码为 `text_output_truncated` 且带出模型时返回，否则返回 null。 */
export function outputTruncationOf(task: Pick<TaskItem, "error_code" | "error_params">): OutputTruncation | null {
  return truncationFromProblem(task.error_code, task.error_params ?? {});
}

/** 同步请求（如生成项目概述）因输出被截断而失败时的截断信息：`diagnostic` 是同一个问题票形状，否则返回 null。 */
export function outputTruncationOfError(err: unknown): OutputTruncation | null {
  if (!(err instanceof ApiRequestError)) return null;
  const problem = err.diagnostic;
  if (typeof problem !== "object" || problem === null) return null;
  const { code, params } = problem as { code?: unknown; params?: unknown };
  if (typeof params !== "object" || params === null) return null;
  return truncationFromProblem(code, params as Record<string, unknown>);
}

/** 自定义供应商模型在设置页的位置：打开这个供应商的编辑表单并定位到这个模型。内置供应商返回 null。 */
export function customModelSettingsPath(providerId: string, model: string): string | null {
  if (!providerId.startsWith(CUSTOM_PROVIDER_PREFIX)) return null;
  const id = providerId.slice(CUSTOM_PROVIDER_PREFIX.length);
  if (!/^\d+$/.test(id)) return null;
  return settingsSectionPath("providers", { custom: id, model });
}
