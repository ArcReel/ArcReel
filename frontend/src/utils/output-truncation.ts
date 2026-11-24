import { ApiRequestError } from "@/api/errors";
import { providerSettingsPath } from "@/app-routes";
import type { TaskItem } from "@/types";

const CUSTOM_PROVIDER_PREFIX = "custom-";

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

export function outputTruncationOf(task: Pick<TaskItem, "error_code" | "error_params">): OutputTruncation | null {
  return truncationFromProblem(task.error_code, task.error_params ?? {});
}

export function outputTruncationOfError(err: unknown): OutputTruncation | null {
  if (!(err instanceof ApiRequestError)) return null;
  const problem = err.diagnostic;
  if (typeof problem !== "object" || problem === null) return null;
  const { code, params } = problem as { code?: unknown; params?: unknown };
  if (typeof params !== "object" || params === null) return null;
  return truncationFromProblem(code, params as Record<string, unknown>);
}

export function customModelSettingsPath(providerId: string, model: string): string | null {
  if (!providerId.startsWith(CUSTOM_PROVIDER_PREFIX)) return null;
  const id = providerId.slice(CUSTOM_PROVIDER_PREFIX.length);
  if (!/^\d+$/.test(id)) return null;
  return providerSettingsPath({ custom: Number(id), model });
}
