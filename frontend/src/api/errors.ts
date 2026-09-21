/**
 * API 错误契约：后端错误响应的结构、类型守卫、错误类与错误说明的格式化。
 * 对外经 `@/api` 再导出，调用方继续从 `@/api` 导入。
 */

import type {
  FailureObservation,
  NarratedVideoDurationAdmission,
  ReferenceProjectionAdmission,
} from "@/types";
import i18n from "@/i18n";

/** Standard error response body from backend (mirrors FastAPI HTTPException detail). */
export interface ErrorResponse {
  /** 后端附加的诊断或修复信息；调用方可按请求语境解析，永不并进 detail 摘要。 */
  diagnostic?: unknown;
  detail:
    | string
    | { msg?: string }[]
    | AgentFailureDetail
    | SpeechAdmission
    | ScriptEditResult
    | ReferenceProjectionAdmission
    | NarratedVideoDurationAdmission;
}

export interface SpeechAdmissionLocation {
  path: (string | number)[];
  line: number | null;
}

export interface SpeechAdmissionProblem {
  code: "mixed_speech" | "needs_replan" | "parse_failed" | "empty_speaker";
  unit_id: string;
  locations: SpeechAdmissionLocation[];
  reason: string;
  action: string;
}

export interface SpeechAdmission {
  allowed: false;
  unit_id: string;
  mode: null;
  problems: SpeechAdmissionProblem[];
}

export interface ScriptEditProblem {
  code: string;
  operation_index: number | null;
  unit_id: string | null;
  locations: SpeechAdmissionLocation[];
  reason: string;
  next_action: string;
}

export interface ScriptEditResult {
  success: boolean;
  script: string;
  episode: number | null;
  before_revision: string;
  revision: string;
  affected_ids: string[];
  problems: ScriptEditProblem[];
}

export class ScriptEditCommandError extends Error {
  readonly code = "script_edit_rejected" as const;

  constructor(public readonly result: ScriptEditResult) {
    super(formatScriptEditResult(result));
    this.name = "ScriptEditCommandError";
  }
}

/** Preserves the structured speech blocker for UI actions and diagnostics. */
export class SpeechAdmissionError extends Error {
  readonly code = "speech_admission_blocked" as const;

  constructor(public readonly admission: SpeechAdmission) {
    super(formatSpeechAdmission(admission));
    this.name = "SpeechAdmissionError";
  }
}

/**
 * 请求失败的通用错误：`message` 是后端给出的产品语言摘要，可直接展示给使用者；
 * `diagnostic` 是可选的诊断或修复信息，由调用方按请求语境解析，不拼进 `message`。
 */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly diagnostic?: unknown,
    /** HTTP 状态码；调用方据此区分「资源已不存在」与瞬时网络/服务错误。 */
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

/** Preserves reference request blockers so the UI can show a repair action. */
export class ReferenceProjectionError extends Error {
  readonly code = "reference_request_projection_blocked" as const;

  constructor(public readonly projection: ReferenceProjectionAdmission) {
    const firstBlocking = projection.problems.find(({ blocking }) => blocking);
    super(firstBlocking?.message || firstBlocking?.code || "reference_request_projection_blocked");
    this.name = "ReferenceProjectionError";
  }
}

/** Preserves current TTS/duration blockers so callers can perform an exact-tier retry. */
export class NarratedVideoDurationError extends Error {
  readonly code = "narrated_video_duration_blocked" as const;

  constructor(public readonly admission: NarratedVideoDurationAdmission) {
    const firstBlocking = admission.problems.find(({ blocking }) => blocking);
    super(firstBlocking?.message || firstBlocking?.code || "narrated_video_duration_blocked");
    this.name = "NarratedVideoDurationError";
  }
}

/** Structured detail returned when the local Agent process cannot start. */
export interface AgentFailureDetail {
  code: "agent_startup_failed";
  message: string;
  failure: FailureObservation;
}

/** Keeps the redacted failure observation attached while remaining a normal Error. */
export class AgentFailureError extends Error {
  readonly code = "agent_startup_failed" as const;

  constructor(
    message: string,
    public readonly failure: FailureObservation,
  ) {
    super(message);
    this.name = "AgentFailureError";
  }
}

/**
 * Error thrown when uploading a source file conflicts with an existing file
 * (HTTP 409). Carries the existing filename and a server-suggested alternative
 * so callers can prompt the user to retry with `on_conflict=rename|replace`.
 */
export class ConflictError extends Error {
  constructor(
    public readonly existing: string,
    public readonly suggestedName: string,
    message: string
  ) {
    super(message);
    this.name = "ConflictError";
  }
}

/** Error payload from the import project endpoint (extends ErrorResponse with import-specific fields). */
export interface ImportErrorPayload {
  detail?: string | { msg?: string }[];
  errors?: string[];
  warnings?: string[];
  conflict_project_name?: string;
  diagnostics?: unknown;
}

/**
 * 从后端 detail 中取一句可读的说明。
 *
 * 后端把 `{ code, message, ... }` 这样的信封当 detail 抛出的场合（如批量入队中途失败后的
 * 撤销结果），只按字符串与数组取字会把已翻译的说明整段丢掉，用户只收到一句「请求失败」。
 */
export function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail || fallback;
  if (Array.isArray(detail) && detail.length > 0) {
    return (
      detail
        .map((e) => (typeof e === "string" ? e : (e as { msg?: string } | null)?.msg))
        .filter(Boolean)
        .join("; ") || fallback
    );
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message) return message;
  }
  return fallback;
}

export function isAgentFailureDetail(value: unknown): value is AgentFailureDetail {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const detail = value as Record<string, unknown>;
  return (
    detail.code === "agent_startup_failed"
    && typeof detail.message === "string"
    && Boolean(detail.failure)
    && typeof detail.failure === "object"
    && !Array.isArray(detail.failure)
  );
}

export function isSpeechAdmission(value: unknown): value is SpeechAdmission {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const detail = value as Record<string, unknown>;
  return (
    detail.allowed === false
    && typeof detail.unit_id === "string"
    && detail.mode === null
    && Array.isArray(detail.problems)
    && detail.problems.length > 0
    && detail.problems.every((problem) => {
      if (!problem || typeof problem !== "object" || Array.isArray(problem)) return false;
      const entry = problem as Record<string, unknown>;
      return (
        ["mixed_speech", "needs_replan", "parse_failed", "empty_speaker"].includes(String(entry.code))
        && typeof entry.unit_id === "string"
        && Array.isArray(entry.locations)
        && entry.locations.every((location) => {
          if (!location || typeof location !== "object" || Array.isArray(location)) return false;
          const field = location as Record<string, unknown>;
          return (
            Array.isArray(field.path)
            && field.path.every((part) => typeof part === "string" || typeof part === "number")
            && (field.line === null || typeof field.line === "number")
          );
        })
        && typeof entry.reason === "string"
        && typeof entry.action === "string"
      );
    })
  );
}

export function isScriptEditResult(value: unknown): value is ScriptEditResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const result = value as Record<string, unknown>;
  return (
    result.success === false
    && typeof result.script === "string"
    && typeof result.revision === "string"
    && Array.isArray(result.problems)
    && result.problems.length > 0
  );
}

export function isReferenceProjectionAdmission(value: unknown): value is ReferenceProjectionAdmission {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const detail = value as Record<string, unknown>;
  return (
    detail.allowed === false
    && detail.kind === "reference_request_projection"
    && typeof detail.unit_id === "string"
    && Array.isArray(detail.problems)
    && detail.problems.length > 0
    && detail.problems.every((problem) => {
      if (!problem || typeof problem !== "object" || Array.isArray(problem)) return false;
      const entry = problem as Record<string, unknown>;
      return (
        typeof entry.code === "string"
        && typeof entry.blocking === "boolean"
        && typeof entry.unit_id === "string"
        && Array.isArray(entry.locations)
        && entry.locations.every((location) => {
          if (!location || typeof location !== "object" || Array.isArray(location)) return false;
          const field = location as Record<string, unknown>;
          return (
            Array.isArray(field.path)
            && field.path.every((part) => typeof part === "string" || typeof part === "number")
            && (field.line === null || typeof field.line === "number")
          );
        })
        && Boolean(entry.params)
        && typeof entry.params === "object"
        && !Array.isArray(entry.params)
        && typeof entry.action === "string"
        && (entry.message === undefined || typeof entry.message === "string")
      );
    })
  );
}

export function isNarratedVideoDurationAdmission(value: unknown): value is NarratedVideoDurationAdmission {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const detail = value as Record<string, unknown>;
  return (
    detail.allowed === false
    && detail.kind === "narrated_video_duration"
    && typeof detail.unit_id === "string"
    && typeof detail.narration_delivery === "object"
    && detail.narration_delivery !== null
    && !Array.isArray(detail.narration_delivery)
    && typeof detail.planned_duration === "number"
    && typeof detail.duration_input === "number"
    && (detail.request_duration === null || typeof detail.request_duration === "number")
    && (
      detail.adjustment === null
      || detail.adjustment === "exact"
      || detail.adjustment === "up"
      || detail.adjustment === "down"
    )
    && Array.isArray(detail.problems)
    && detail.problems.length > 0
    && detail.problems.every((problem) => {
      if (!problem || typeof problem !== "object" || Array.isArray(problem)) return false;
      const entry = problem as Record<string, unknown>;
      return (
        typeof entry.code === "string"
        && typeof entry.blocking === "boolean"
        && typeof entry.unit_id === "string"
        && Array.isArray(entry.locations)
        && Boolean(entry.params)
        && typeof entry.params === "object"
        && !Array.isArray(entry.params)
        && typeof entry.action === "string"
        && (entry.message === undefined || typeof entry.message === "string")
      );
    })
  );
}

function formatSpeechAdmission(admission: SpeechAdmission): string {
  const problem = admission.problems.find(({ code }) => code !== "needs_replan") ?? admission.problems[0];
  const location = problem.locations
    .map(({ path, line }) => `${path.join(".")}${line === null ? "" : `:${line + 1}`}`)
    .join(", ");
  const key = {
    mixed_speech: "speech_admission_mixed_speech",
    needs_replan: "speech_admission_needs_replan",
    parse_failed: "speech_admission_parse_failed",
    empty_speaker: "speech_admission_empty_speaker",
  }[problem.code];
  return i18n.t(`dashboard:${key}`, { unitId: problem.unit_id, location });
}

function formatScriptEditResult(result: ScriptEditResult): string {
  const first = result.problems[0];
  if (!first) return i18n.t("dashboard:script_edit_rejected");
  const speechCodes: SpeechAdmissionProblem["code"][] = [
    "mixed_speech",
    "needs_replan",
    "parse_failed",
    "empty_speaker",
  ];
  if (speechCodes.includes(first.code as SpeechAdmissionProblem["code"]) && first.unit_id !== null) {
    const unitId = first.unit_id;
    const problems = result.problems
      .filter(({ code, unit_id }) => (
        unit_id === unitId && speechCodes.includes(code as SpeechAdmissionProblem["code"])
      ))
      .map((problem) => ({
        code: problem.code as SpeechAdmissionProblem["code"],
        unit_id: problem.unit_id ?? unitId,
        locations: problem.locations,
        reason: problem.reason,
        action: problem.next_action,
      }));
    return formatSpeechAdmission({ allowed: false, unit_id: unitId, mode: null, problems });
  }
  const key = {
    revision_conflict: "script_edit_revision_conflict",
    operation_invalid: "script_edit_operation_invalid",
    schema_invalid: "script_edit_schema_invalid",
    references_invalid: "script_edit_references_invalid",
    manifest_invalid: "script_edit_manifest_invalid",
    commit_failed: "script_edit_commit_failed",
  }[first.code] ?? "script_edit_rejected";
  return i18n.t(`dashboard:${key}`);
}

export class ReadOnlyModeError extends Error {
  constructor(method: string) {
    super(`Blocked ${method} request: the workspace is in read-only demo mode`);
    this.name = "ReadOnlyModeError";
  }
}
