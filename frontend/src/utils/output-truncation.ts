import { ApiRequestError } from "@/api/errors";
import { providerSettingsPath } from "@/app-routes";
import type { TaskItem } from "@/types";

const CUSTOM_PROVIDER_PREFIX = "custom-";

/** Model for a text task failing from truncated output; `custom` marks custom-provider models whose maximum output length can be registered in settings. */
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

/** Failed-task truncation information: return when the problem code is `text_output_truncated` and includes a model; otherwise null. */
export function outputTruncationOf(task: Pick<TaskItem, "error_code" | "error_params">): OutputTruncation | null {
  return truncationFromProblem(task.error_code, task.error_params ?? {});
}

/** Truncation information for synchronous requests such as project-overview generation: `diagnostic` has the same problem-ticket shape; otherwise null. */
export function outputTruncationOfError(err: unknown): OutputTruncation | null {
  if (!(err instanceof ApiRequestError)) return null;
  const problem = err.diagnostic;
  if (typeof problem !== "object" || problem === null) return null;
  const { code, params } = problem as { code?: unknown; params?: unknown };
  if (typeof params !== "object" || params === null) return null;
  return truncationFromProblem(code, params as Record<string, unknown>);
}

/** Custom-provider model location in settings: open that provider's editor and locate the model. Return null for built-in providers. */
export function customModelSettingsPath(providerId: string, model: string): string | null {
  if (!providerId.startsWith(CUSTOM_PROVIDER_PREFIX)) return null;
  const id = providerId.slice(CUSTOM_PROVIDER_PREFIX.length);
  if (!/^\d+$/.test(id)) return null;
  return providerSettingsPath({ custom: Number(id), model });
}
