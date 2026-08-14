/**
 * API 调用封装 (TypeScript)
 *
 * Typed API layer for all backend endpoints.
 * Import: import { API } from '@/api';
 */

import type {
  ProjectData,
  ProjectSummary,
  ImportConflictPolicy,
  ImportProjectResponse,
  ExportDiagnostics,
  ImportFailureDiagnostics,
  EpisodeScript,
  TaskItem,
  TaskStats,
  SessionMeta,
  ImagePayload,
  EntriesResponse,
  TimelineEntry,
  FailureObservation,
  SkillInfo,
  ProjectOverview,
  ProjectChangeBatchPayload,
  ProjectEventSnapshotPayload,
  ProjectDeletedPayload,
  GetSystemConfigResponse,
  GetSystemVersionResponse,
  ModelCandidatesResponse,
  OnboardingStatus,
  SystemConfigPatch,
  ApiKeyInfo,
  CreateApiKeyResponse,
  ProviderInfo,
  ProviderConfigDetail,
  ProviderTestResult,
  ProviderCredential,
  UsageStatsResponse,
  CustomProviderInfo,
  CustomProviderModelInfo,
  CustomProviderCreateRequest,
  CustomProviderFullUpdateRequest,
  CustomProviderModelInput,
  DiscoveredModel,
  EndpointDescriptor,
  CustomProviderCredentials,
  AnthropicDiscoverRequest,
  AnthropicDiscoverResponse,
  CostEstimateResponse,
  ReferenceVideoUnit,
  ReferenceResource,
  TransitionType,
  AdShot,
  ReferenceDurationPrecheck,
  ReferenceProjectionAdmission,
  NarratedVideoDurationAdmission,
  ReferenceGenerationRequestOptions,
  ReferenceRequestOptions,
  ScriptPreview,
  ScriptReviewState,
  DramaNormalizedScript,
  NarrationStep1Draft,
  ReferenceStep1Draft,
  VideoCapabilities,
} from "@/types";
import type { GenerationRoute } from "@/utils/generation-mode";
import type { GridCapability, GridGeneration } from "@/types/grid";
import type {
  PresentationReadModel,
  PresentationRequestOptions,
  PresentationResourceType,
} from "@/types/presentation";
import type { Asset, AssetType, AssetCreatePayload, AssetUpdatePayload } from "@/types/asset";
import type {
  AgentCredential,
  CreateAgentCredentialRequest,
  PresetProvidersResponse,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateAgentCredentialRequest,
} from "@/types/agent-credential";
import { getToken, clearToken } from "@/utils/auth";
import { isDemoProject } from "@/onboarding/demo-project";
import i18n from "./i18n";

// ==================== Helper types ====================

/** 项目内四类资产（与后端 ASSET_SPECS 的 asset_type 对齐）。 */
export type ProjectAssetType = "character" | "scene" | "prop" | "product";

/** asset_type → REST 路径段（与后端 spec.subdir 对齐）。 */
const ASSET_TYPE_PATH: Record<ProjectAssetType, string> = {
  character: "characters",
  scene: "scenes",
  prop: "props",
  product: "products",
};

function referenceRequestQuery(
  options: ReferenceRequestOptions,
  initial?: Record<string, string>,
): string {
  const query = new URLSearchParams(initial);
  if (options.narration_delivery) {
    query.set("narration_delivery", options.narration_delivery);
  }
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

function presentationEndpoint(
  projectName: string,
  resourceType: PresentationResourceType,
  resourceId: string,
  options: PresentationRequestOptions,
  suffix = "",
): string {
  const query = new URLSearchParams({ variant: options.variant ?? "post_production" });
  if (options.videoVersion !== undefined) query.set("video_version", String(options.videoVersion));
  if (options.audioVersion !== undefined) query.set("audio_version", String(options.audioVersion));
  return `/projects/${encodeURIComponent(projectName)}/presentations/${resourceType}/${encodeURIComponent(resourceId)}${suffix}?${query.toString()}`;
}

/** 资产级联重命名的影响报告（dry_run 预览与执行同一结构）。 */
export interface AssetRenameResult {
  success: boolean;
  dry_run: boolean;
  old_name: string;
  new_name: string;
  episodes: number;
  references: number;
  files: number;
}

/** Login response from POST /auth/token (mirrors backend TokenResponse). */
export interface LoginResponse {
  access_token: string;
  token_type: string;
}

/** Standard error response body from backend (mirrors FastAPI HTTPException detail). */
export interface ErrorResponse {
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

export type ScriptEditOperation =
  | { op: "update"; id: string; fields: Record<string, unknown> }
  | { op: "insert_after"; after_id: string | null; item: Record<string, unknown> }
  | { op: "move_after"; id: string; after_id: string | null }
  | { op: "remove"; id: string };

export interface ScriptEditCommand {
  script?: string;
  episode?: number;
  expected_revision: string;
  operations: ScriptEditOperation[];
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

export interface EpisodeScriptSnapshot {
  script: EpisodeScript;
  revision: string;
}

/** Preserves the structured speech blocker for UI actions and diagnostics. */
export class SpeechAdmissionError extends Error {
  readonly code = "speech_admission_blocked" as const;

  constructor(public readonly admission: SpeechAdmission) {
    super(formatSpeechAdmission(admission));
    this.name = "SpeechAdmissionError";
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
interface ImportErrorPayload {
  detail?: string | { msg?: string }[];
  errors?: string[];
  warnings?: string[];
  conflict_project_name?: string;
  diagnostics?: unknown;
}

/** Version metadata returned by the versions API. */
export interface VersionInfo {
  version: number;
  filename: string;
  created_at: string;
  file_size: number;
  is_current: boolean;
  /** Whether this history record carries verified provenance for restore. */
  restorable?: boolean;
  /** Whether the shared presentation reader can preview/export this video version. */
  presentation_available?: boolean;
  file_url?: string;
  prompt?: string;
  restored_from?: number;
  /** 版本来源标记；"manual_upload" 表示用户手动上传 */
  source?: string;
}

/** 镜头/单元媒体上传的统一响应。 */
export interface ShotUploadResult {
  success: boolean;
  path: string;
  version: number;
  asset_fingerprints: Record<string, number>;
}

export interface ProjectEventStreamOptions {
  projectName: string;
  onSnapshot?: (payload: ProjectEventSnapshotPayload, event: MessageEvent) => void;
  onChanges?: (payload: ProjectChangeBatchPayload, event: MessageEvent) => void;
  /** 项目目录被删除后收到一次，随后流正常结束（浏览器会紧接着触发一次 onError）。 */
  onProjectDeleted?: (payload: ProjectDeletedPayload, event: MessageEvent) => void;
  onError?: (event: Event) => void;
}

/** Filters for {@link API.listTasks} and {@link API.listProjectTasks}. */
export interface TaskListFilters {
  projectName?: string;
  status?: string;
  taskType?: string;
  source?: string;
  page?: number;
  pageSize?: number;
}

/** Filters for {@link API.getUsageStats} and {@link API.getUsageCalls}. */
export interface UsageStatsFilters {
  projectName?: string;
  startDate?: string;
  endDate?: string;
}

export interface UsageCallsFilters {
  projectName?: string;
  callType?: string;
  status?: string;
  startDate?: string;
  endDate?: string;
  page?: number;
  pageSize?: number;
}

/** Generic success response used by many endpoints. */
export interface SuccessResponse {
  success: boolean;
  message?: string;
}

export interface AgentProfileStatus {
  customized: boolean;
  customized_files: string[];
}

/** 说书模式片段 PATCH 入参（drama 模式片段走 {@link API.updateScene}）。 */
export interface SegmentUpdatePayload {
  script_file: string;
  duration_seconds?: number;
  segment_break?: boolean;
  image_prompt?: unknown;
  video_prompt?: unknown;
  transition_to_next?: string;
  note?: string;
  characters_in_segment?: string[];
  scenes?: string[];
  props?: string[];
}

/** Payload for {@link API.createProject}. */
export interface CreateProjectPayload {
  title: string;
  name?: string;
  creation_type?: "narration" | "drama" | "ad";
  /** 源文件性质：novel（默认）/ screenplay。仅 drama 暴露，创建即定、不可变。 */
  source_file_type?: "novel" | "screenplay";
  aspect_ratio?: "9:16" | "16:9";
  /** 生成路线，创建时必填二选一、无默认值（后端缺失即 422）。 */
  generation_mode: GenerationRoute;
  /** 分镜板（宫格）装配开关，可随创建写入；仅分镜路线有意义。 */
  grid_storyboard?: boolean;
  /** 口播语速估算（阅读单位 / 秒）；留空即按项目语言的默认速度估算。 */
  speech_rate_units_per_second?: number | null;
  default_duration?: number | null;
  /** 仅 ad：目标总时长（秒），UI 四档 15/30/60/90。 */
  target_duration?: number;
  /** 仅 ad：创作诉求短文本（可空）。 */
  brief?: string | null;
  style_template_id?: string | null;
  video_backend?: string | null;
  image_backend?: string | null;
  /** 项目默认图片模型。创建向导只暴露默认层（docs/adr/0054），能力桶留给项目设置页。 */
  default_image_backend?: string | null;
  text_backend_simple?: string | null;
  text_backend_complex?: string | null;
  default_text_backend?: string | null;
  model_settings?: Record<string, { resolution?: string | null }>;
}

function normalizeDiagnosticsBucket(value: unknown): { code: string; message: string; location?: string }[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter(
      (item): item is { code: string; message: string; location?: string } =>
        Boolean(item)
        && typeof item === "object"
        && typeof (item as { code?: unknown }).code === "string"
        && typeof (item as { message?: unknown }).message === "string"
    )
    .map((item) => ({
      code: item.code,
      message: item.message,
      ...(typeof item.location === "string" ? { location: item.location } : {}),
    }));
}

function normalizeImportFailureDiagnostics(value: unknown): ImportFailureDiagnostics {
  const payload = (value && typeof value === "object") ? value as Record<string, unknown> : {};
  return {
    blocking: normalizeDiagnosticsBucket(payload.blocking),
    auto_fixable: normalizeDiagnosticsBucket(payload.auto_fixable),
    warnings: normalizeDiagnosticsBucket(payload.warnings),
  };
}

function normalizeExportDiagnostics(value: unknown): ExportDiagnostics {
  const payload = (value && typeof value === "object") ? value as Record<string, unknown> : {};
  return {
    blocking: normalizeDiagnosticsBucket(payload.blocking),
    auto_fixed: normalizeDiagnosticsBucket(payload.auto_fixed),
    warnings: normalizeDiagnosticsBucket(payload.warnings),
  };
}

// ==================== API class ====================

const API_BASE = "/api/v1";

/**
 * 检查 fetch 响应状态，抛出包含后端错误信息的 Error。
 * 用于不经过 API.request() 的自定义 fetch 调用。
 */
async function throwIfNotOk(response: Response, fallbackMsg: string): Promise<void> {
  if (!response.ok) {
    handleUnauthorized(response);
    const error = await response
      .json()
      .catch(() => ({ detail: response.statusText })) as ErrorResponse;
    const detail = error.detail;
    if (isReferenceProjectionAdmission(detail)) {
      throw new ReferenceProjectionError(detail);
    }
    if (isNarratedVideoDurationAdmission(detail)) {
      throw new NarratedVideoDurationError(detail);
    }
    if (isSpeechAdmission(detail)) {
      throw new SpeechAdmissionError(detail);
    }
    throw new Error(typeof detail === "string" ? detail || fallbackMsg : fallbackMsg);
  }
}

function handleUnauthorized(response: Response): void {
  if (response.status !== 401) return;

  clearToken();
  // 携带当前所在的站内地址，登录成功后回跳；仅对 /app/ 下的页面附加 from，
  // 避免把登录页自身等非应用路径写进回跳参数。
  const current = `${globalThis.location.pathname}${globalThis.location.search}${globalThis.location.hash}`;
  globalThis.location.href = current.startsWith("/app/")
    ? `/login?from=${encodeURIComponent(current)}`
    : "/login";
  throw new Error("认证已过期，请重新登录");
}

function isAgentFailureDetail(value: unknown): value is AgentFailureDetail {
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

function isSpeechAdmission(value: unknown): value is SpeechAdmission {
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

function isScriptEditResult(value: unknown): value is ScriptEditResult {
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

function isReferenceProjectionAdmission(value: unknown): value is ReferenceProjectionAdmission {
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

function isNarratedVideoDurationAdmission(value: unknown): value is NarratedVideoDurationAdmission {
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

/** 为 fetch options 注入 Authorization header */
let apiReadOnly = false;

/**
 * 进入 / 离开只读态（引导演示工作台）。只读期间任何非 GET / HEAD 请求会在发出前被拒绝。
 *
 * 演示工作台是用真组件渲染假数据，写操作的入口都已经不渲染；这道闸门是结构性兜底 ——
 * 漏掉一个入口时会得到一个明确的异常，而不是一条真写进用户项目的请求。
 */
export function setApiReadOnly(readOnly: boolean): void {
  apiReadOnly = readOnly;
}

export class ReadOnlyModeError extends Error {
  constructor(method: string) {
    super(`Blocked ${method} request: the workspace is in read-only demo mode`);
    this.name = "ReadOnlyModeError";
  }
}

// 静态归档导入端点，不是「项目名恰好叫 import」——项目名允许字母数字中划线，
// `import` 本身是合法项目名（ProjectManager.normalize_project_name 不排除它），
// 按精确路径匹配而非按名称黑名单，避免把 `/projects/import/...`（真实项目名为
// import 的写请求）一并误判成不带项目归属
const RESERVED_PROJECT_ENDPOINTS = new Set(["/projects/import"]);

// 不带项目归属、但本身不写入任何项目数据的系统级端点：闸门默认拦截所有无项目归属的
// 写请求（全局资产库、供应商凭证等），这里是唯一的窄豁免。引导 tour 退出时会在仍处于
// 演示路由（apiReadOnly 尚未复位）期间写这一条「已看过」标记，它只影响当前用户的引导
// 状态，不属于闸门要防的「误写演示态/其他项目数据」范畴。
const READ_ONLY_GATE_EXEMPT_ENDPOINTS = new Set(["/onboarding/seen"]);

/** 从形如 `/projects/{name}` 或 `/projects/{name}/...` 的 endpoint 中取出项目名；非项目路径或静态保留端点返回 null */
function extractProjectName(endpoint: string): string | null {
  if (RESERVED_PROJECT_ENDPOINTS.has(endpoint)) return null;
  const match = /^\/projects\/([^/?]+)/.exec(endpoint);
  if (!match) return null;
  return decodeURIComponent(match[1]);
}

/**
 * 只读闸门是否应拦截这次请求。指向某个具体真实项目的写请求放行 ——
 * 演示态可能在该请求发出前才切入，但它拦不住已经从真实项目发起的操作；
 * 指向演示项目本身或不带项目归属的请求（全局资产库等）仍按闸门原意拦截，
 * 窄豁免名单中的系统端点除外。
 */
function isReadOnlyGateBlocking(endpoint: string): boolean {
  if (!apiReadOnly) return false;
  if (READ_ONLY_GATE_EXEMPT_ENDPOINTS.has(endpoint)) return false;
  const projectName = extractProjectName(endpoint);
  return projectName === null || isDemoProject(projectName);
}

function withAuth(endpoint: string, options: RequestInit = {}): RequestInit {
  const method = (options.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && isReadOnlyGateBlocking(endpoint)) {
    throw new ReadOnlyModeError(method);
  }
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // Add Accept-Language header based on current i18n language
  headers.set("Accept-Language", i18n.language || "zh");
  return { ...options, headers };
}

/** 为 URL 追加 token query param（用于 EventSource） */
function withAuthQuery(url: string): string {
  const token = getToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

class API {
  /**
   * 通用请求方法
   */
  static async request<T = unknown>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_BASE}${endpoint}`;
    const defaultOptions: RequestInit = {
      headers: {
        "Content-Type": "application/json",
      },
    };

    const response = await fetch(url, withAuth(endpoint, { ...defaultOptions, ...options }));

    if (!response.ok) {
      handleUnauthorized(response);
      const payload = await response
        .json()
        .catch(() => ({ detail: response.statusText })) as unknown;
      if (isScriptEditResult(payload)) {
        throw new ScriptEditCommandError(payload);
      }
      const error = payload as ErrorResponse;
      if (isScriptEditResult(error.detail)) {
        throw new ScriptEditCommandError(error.detail);
      }
      if (isAgentFailureDetail(error.detail)) {
        throw new AgentFailureError(error.detail.message, error.detail.failure);
      }
      if (isReferenceProjectionAdmission(error.detail)) {
        throw new ReferenceProjectionError(error.detail);
      }
      if (isNarratedVideoDurationAdmission(error.detail)) {
        throw new NarratedVideoDurationError(error.detail);
      }
      if (isSpeechAdmission(error.detail)) {
        throw new SpeechAdmissionError(error.detail);
      }
      let message = "请求失败";
      if (typeof error.detail === "string") {
        message = error.detail;
      } else if (Array.isArray(error.detail) && error.detail.length > 0) {
        message = error.detail.map((e) => (typeof e === "string" ? e : e?.msg)).filter(Boolean).join("; ") || message;
      }
      throw new Error(message);
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return response.json() as Promise<T>;
  }

  // ==================== 系统配置 ====================

  static async getSystemConfig(): Promise<GetSystemConfigResponse> {
    return this.request("/system/config");
  }

  /**
   * 能力桶下拉的候选数据源（docs/adr/0054）：默认层全量 + 每个桶按能力过滤后的模型列表。
   * 与 getSystemConfig 的 options 同口径（同样剔除 hidden 模型），过滤只加在桶层。
   */
  static async getModelCandidates(
    options: { signal?: AbortSignal } = {}
  ): Promise<ModelCandidatesResponse> {
    return this.request("/system/config/model-candidates", { signal: options.signal });
  }

  static async getSystemVersion(): Promise<GetSystemVersionResponse> {
    return this.request("/system/version");
  }

  // ==================== 首次使用引导 ====================

  static async getOnboardingStatus(
    options: { signal?: AbortSignal } = {}
  ): Promise<OnboardingStatus> {
    return this.request("/onboarding/status", { signal: options.signal });
  }

  static async markOnboardingSeen(): Promise<OnboardingStatus> {
    return this.request("/onboarding/seen", { method: "POST" });
  }

  static async downloadDiagnostics(): Promise<{ blob: Blob; filename: string }> {
    const response = await fetch(
      `${API_BASE}/system/logs/download`,
      withAuth("/system/logs/download", { method: "GET" }),
    );
    await throwIfNotOk(response, `HTTP ${response.status}`);
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = disposition.match(/filename="?([^";]+)"?/);
    const filename = match?.[1] ?? "arcreel-diagnostics.zip";
    const blob = await response.blob();
    return { blob, filename };
  }

  static async updateSystemConfig(
    patch: SystemConfigPatch,
  ): Promise<GetSystemConfigResponse> {
    return this.request("/system/config", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }


  // ==================== 项目管理 ====================

  static async listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return this.request("/projects");
  }

  static async createProject(
    payload: CreateProjectPayload,
  ): Promise<{ success: boolean; name: string; project: ProjectData }> {
    return this.request("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static async getProject(
    name: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<{
    project: ProjectData;
    scripts: Record<string, EpisodeScript>;
    asset_fingerprints?: Record<string, number>;
  }> {
    return this.request(`/projects/${encodeURIComponent(name)}`, { signal: options.signal });
  }

  static async updateProject(
    name: string,
    updates: Partial<ProjectData> & { clear_style_image?: boolean }
  ): Promise<{ success: boolean; project: ProjectData }> {
    if ("creation_type" in updates || "content_mode" in updates) {
      throw new Error("项目创建后不支持修改 creation_type");
    }
    if ("source_file_type" in updates || "source_kind" in updates) {
      throw new Error("项目创建后不支持修改 source_file_type");
    }
    return this.request(`/projects/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    });
  }

  static async getAgentProfileStatus(name: string): Promise<AgentProfileStatus> {
    return this.request(`/projects/${encodeURIComponent(name)}/agent-profile`);
  }

  static async resetAgentProfile(name: string): Promise<AgentProfileStatus> {
    return this.request(`/projects/${encodeURIComponent(name)}/agent-profile/reset`, {
      method: "POST",
    });
  }

  static async deleteProject(name: string): Promise<SuccessResponse> {
    return this.request(`/projects/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  }

  /**
   * 三级解析（项目 > 系统设置 > 系统默认）后的视频模型能力。
   *
   * `videoBackend`（"provider/model"）用于设置表单里尚未保存的候选模型：不传按已落盘配置
   * 解析，传了则按该候选模型 × 本项目生效 generation_mode 解析。
   *
   * `episode` 用于按集查看的界面：生成模式可被单集覆盖，传集号则能力按该集生效模式解析，
   * 与执行层同口径；不传只解析到项目级（设置页等无集号上下文的调用）。
   */
  static async getVideoCapabilities(
    name: string,
    options: { signal?: AbortSignal; videoBackend?: string; episode?: number } = {}
  ): Promise<VideoCapabilities> {
    const params = new URLSearchParams();
    if (options.videoBackend) params.set("video_backend", options.videoBackend);
    if (options.episode !== undefined) params.set("episode", String(options.episode));
    const qs = params.size > 0 ? `?${params.toString()}` : "";
    return this.request(`/projects/${encodeURIComponent(name)}/video-capabilities${qs}`, {
      signal: options.signal,
    });
  }

  static async requestExportToken(
    projectName: string,
    scope: "full" | "current" = "full"
  ): Promise<{ download_token: string; expires_in: number; diagnostics: ExportDiagnostics }> {
    const payload = await this.request<{
      download_token: string;
      expires_in: number;
      diagnostics?: unknown;
    }>(
      `/projects/${encodeURIComponent(projectName)}/export/token?scope=${encodeURIComponent(scope)}`,
      {
        method: "POST",
      }
    );
    return {
      download_token: payload.download_token,
      expires_in: payload.expires_in,
      diagnostics: normalizeExportDiagnostics(payload.diagnostics),
    };
  }

  static getExportDownloadUrl(
    projectName: string,
    downloadToken: string,
    scope: "full" | "current" = "full"
  ): string {
    return `${API_BASE}/projects/${encodeURIComponent(projectName)}/export?download_token=${encodeURIComponent(downloadToken)}&scope=${encodeURIComponent(scope)}`;
  }

  /** 构造剪映草稿下载 URL */
  static getJianyingDraftDownloadUrl(
    projectName: string,
    episode: number,
    draftPath: string,
    downloadToken: string,
    jianyingVersion: string = "6",
    narrationDelivery: "post_production" | "use_tts" = "post_production",
  ): string {
    return `${API_BASE}/projects/${encodeURIComponent(projectName)}/export/jianying-draft?episode=${encodeURIComponent(episode)}&draft_path=${encodeURIComponent(draftPath)}&download_token=${encodeURIComponent(downloadToken)}&jianying_version=${encodeURIComponent(jianyingVersion)}&narration_delivery=${encodeURIComponent(narrationDelivery)}`;
  }

  static async getPresentation(
    projectName: string,
    resourceType: PresentationResourceType,
    resourceId: string,
    options: PresentationRequestOptions = {},
  ): Promise<PresentationReadModel> {
    const endpoint = presentationEndpoint(projectName, resourceType, resourceId, options);
    return this.request(endpoint, { signal: options.signal });
  }

  static async downloadPresentationBundle(
    projectName: string,
    resourceType: PresentationResourceType,
    resourceId: string,
    options: PresentationRequestOptions = {},
  ): Promise<{ blob: Blob; filename: string }> {
    const endpoint = presentationEndpoint(projectName, resourceType, resourceId, options, "/bundle");
    const response = await fetch(`${API_BASE}${endpoint}`, withAuth(endpoint));
    await throwIfNotOk(response, `HTTP ${response.status}`);
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] ?? `${resourceId}_presentation.zip`;
    return { blob: await response.blob(), filename };
  }

  static async importProject(
    file: File,
    conflictPolicy: ImportConflictPolicy = "prompt"
  ): Promise<ImportProjectResponse> {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("conflict_policy", conflictPolicy);

    const response = await fetch(
      `${API_BASE}/projects/import`,
      withAuth("/projects/import", {
        method: "POST",
        body: formData,
      })
    );

    if (!response.ok) {
      handleUnauthorized(response);

      const payload = await response
        .json()
        .catch(() => ({ detail: response.statusText, errors: [], warnings: [] })) as ImportErrorPayload;
      const error = new Error(
        typeof payload.detail === "string" ? payload.detail : "导入失败"
      ) as Error & {
        status?: number;
        detail?: string;
        errors?: string[];
        warnings?: string[];
        conflict_project_name?: string;
        diagnostics?: ImportFailureDiagnostics;
      };
      error.status = response.status;
      error.detail = typeof payload.detail === "string" ? payload.detail : "导入失败";
      error.errors = Array.isArray(payload.errors) ? payload.errors : [];
      error.warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      if (typeof payload.conflict_project_name === "string") {
        error.conflict_project_name = payload.conflict_project_name;
      }
      error.diagnostics = normalizeImportFailureDiagnostics(payload.diagnostics);
      throw error;
    }

    const payload = await response.json() as ImportProjectResponse & { diagnostics?: { auto_fixed?: unknown[]; warnings?: unknown[] } };
    return {
      ...payload,
      diagnostics: {
        auto_fixed: normalizeDiagnosticsBucket(payload?.diagnostics?.auto_fixed),
        warnings: normalizeDiagnosticsBucket(payload?.diagnostics?.warnings),
      },
    };
  }

  // ==================== 角色管理 ====================

  static async addCharacter(
    projectName: string,
    name: string,
    description: string,
    voiceStyle: string = ""
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters`,
      {
        method: "POST",
        body: JSON.stringify({
          name,
          description,
          voice_style: voiceStyle,
        }),
      }
    );
  }

  static async updateCharacter(
    projectName: string,
    charName: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(charName)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  static async deleteCharacter(
    projectName: string,
    charName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(charName)}`,
      {
        method: "DELETE",
      }
    );
  }

  // ==================== 项目场景管理 ====================

  static async addProjectScene(
    projectName: string,
    name: string,
    description: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scenes`,
      {
        method: "POST",
        body: JSON.stringify({ name, description }),
      }
    );
  }

  static async updateProjectScene(
    projectName: string,
    sceneName: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scenes/${encodeURIComponent(sceneName)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  static async deleteProjectScene(
    projectName: string,
    sceneName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scenes/${encodeURIComponent(sceneName)}`,
      {
        method: "DELETE",
      }
    );
  }

  // ==================== 项目道具管理 ====================

  static async addProjectProp(
    projectName: string,
    name: string,
    description: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/props`,
      {
        method: "POST",
        body: JSON.stringify({ name, description }),
      }
    );
  }

  static async updateProjectProp(
    projectName: string,
    propName: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propName)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  static async deleteProjectProp(
    projectName: string,
    propName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propName)}`,
      {
        method: "DELETE",
      }
    );
  }

  // ==================== 项目产品管理 ====================

  static async addProjectProduct(
    projectName: string,
    name: string,
    description: string,
    brand?: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/products`,
      {
        method: "POST",
        body: JSON.stringify(brand ? { name, description, brand } : { name, description }),
      }
    );
  }

  static async updateProjectProduct(
    projectName: string,
    productName: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/products/${encodeURIComponent(productName)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  static async deleteProjectProduct(
    projectName: string,
    productName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/products/${encodeURIComponent(productName)}`,
      {
        method: "DELETE",
      }
    );
  }

  // ==================== 项目资产重命名 ====================

  /**
   * 级联重命名项目内资产。`dryRun: true` 只返回影响预览（将更新的集数/引用处数/文件数），
   * 预览与执行共用后端同一套扫描逻辑，确认框数字与实际执行一致。
   */
  static async renameProjectAsset(
    projectName: string,
    assetType: ProjectAssetType,
    name: string,
    newName: string,
    options: { dryRun?: boolean; signal?: AbortSignal } = {}
  ): Promise<AssetRenameResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/${ASSET_TYPE_PATH[assetType]}/${encodeURIComponent(name)}/rename`,
      {
        method: "POST",
        body: JSON.stringify({ new_name: newName, dry_run: options.dryRun ?? false }),
        signal: options.signal,
      }
    );
  }

  // ==================== 场景管理 ====================

  static async getScript(
    projectName: string,
    scriptFile: string
  ): Promise<EpisodeScriptSnapshot> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scripts/${encodeURIComponent(scriptFile)}`
    );
  }

  /** Revisioned, ordered, all-or-nothing episode-script edit command. */
  static async editScriptBatch(
    projectName: string,
    command: ScriptEditCommand
  ): Promise<ScriptEditResult> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/script-edits`, {
      method: "POST",
      body: JSON.stringify(command),
    });
  }

  static async updateScene(
    projectName: string,
    sceneId: string,
    scriptFile: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/script-scenes/${encodeURIComponent(sceneId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ script_file: scriptFile, updates }),
      }
    );
  }

  /** 更新分集顶层元数据（当前仅 title）。以剧本顶层 title 为唯一真相源，后端会镜像到 project.json。 */
  static async updateEpisode(
    projectName: string,
    episode: number,
    updates: { title: string }
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  // ==================== step1→step2 审核 gate ====================

  /** 读取该集 step1 结构化中间态 + 审核状态（供 web 渲染与编辑）。 */
  static async getScriptReview(
    projectName: string,
    episode: number,
    options: { signal?: AbortSignal } = {}
  ): Promise<ScriptReviewState> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/script-review`,
      { signal: options.signal }
    );
  }

  /** 保存手动 / agent 编辑后的结构化中间态，返回最新状态（重新待审）。
   *
   * `baseFingerprint` 传 GET 时拿到的内容指纹：编辑期间 step1 被另一写入方（如 agent 晋升）
   * 改过时服务端 409 冲突、不落盘，避免静默覆盖对方的修改；不传则不比对。 */
  static async saveScriptReviewContent(
    projectName: string,
    episode: number,
    content: DramaNormalizedScript | NarrationStep1Draft | ReferenceStep1Draft,
    baseFingerprint?: string | null
  ): Promise<ScriptReviewState> {
    const query = baseFingerprint
      ? `?base_fingerprint=${encodeURIComponent(baseFingerprint)}`
      : "";
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/script-review/content${query}`,
      {
        method: "PUT",
        body: JSON.stringify(content),
      }
    );
  }

  /** 用户显式确认 step1 内容，放行 step2 视觉生成。 */
  static async confirmScriptReview(
    projectName: string,
    episode: number
  ): Promise<ScriptReviewState> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/script-review/confirm`,
      { method: "POST" }
    );
  }

  // ==================== 片段管理（说书模式） ====================

  /** `updates` 字段形状参见 {@link SegmentUpdatePayload}；保留 Record 以兼容 spread 调用。 */
  static async updateSegment(
    projectName: string,
    segmentId: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/segments/${encodeURIComponent(segmentId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  // ==================== 镜头管理（广告/短片模式） ====================

  /** 更新 ad 剧本中的单个镜头（口播文案 / section / 时长 / 引用列表等白名单字段）。 */
  static async updateShot(
    projectName: string,
    shotId: string,
    scriptFile: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse & { shot?: AdShot }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/script-shots/${encodeURIComponent(shotId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ script_file: scriptFile, updates }),
      }
    );
  }

  /** 按给定全排列重排 ad 剧本的镜头顺序。 */
  static async reorderShots(
    projectName: string,
    scriptFile: string,
    shotIds: string[]
  ): Promise<SuccessResponse & { shots?: AdShot[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/script-shots/reorder`,
      {
        method: "POST",
        body: JSON.stringify({ script_file: scriptFile, shot_ids: shotIds }),
      }
    );
  }

  // ==================== 文件管理 ====================

  static async uploadFile(
    projectName: string,
    uploadType: string,
    file: File,
    name: string | null = null,
    options: { onConflict?: "fail" | "replace" | "rename" } = {}
  ): Promise<{
    success: boolean;
    path: string;
    url: string;
    filename?: string;
    normalized?: boolean;
    original_kept?: boolean;
    original_filename?: string;
    used_encoding?: string | null;
    chapter_count?: number;
  }> {
    const formData = new FormData();
    formData.append("file", file);

    const qsParts: string[] = [];
    if (name) qsParts.push(`name=${encodeURIComponent(name)}`);
    if (uploadType === "source" && options.onConflict) {
      qsParts.push(`on_conflict=${encodeURIComponent(options.onConflict)}`);
    }
    const qs = qsParts.join("&");
    const url = `/projects/${encodeURIComponent(projectName)}/upload/${uploadType}${qs ? "?" + qs : ""}`;

    const response = await fetch(`${API_BASE}${url}`, withAuth(url, {
      method: "POST",
      body: formData,
    }));

    if (response.status === 409) {
      let detail: { existing?: string; suggested_name?: string; message?: string } | null = null;
      try {
        const body = (await response.json()) as { detail?: { existing?: string; suggested_name?: string; message?: string } };
        detail = body?.detail ?? null;
      } catch {
        /* ignore */
      }
      // 后端 SourceLoader 的 ConflictError 必然携带 existing + suggested_name；
      // 若 detail 缺字段则视为协议异常，抛通用错误（带文件名标识）而非手搓 fallback —
      // 避免前端"猜"一个可能与后端命名规则不一致的 suggested_name 误导用户
      if (!detail?.existing || !detail?.suggested_name) {
        throw new Error(`上传 "${file.name}" 失败：服务端返回 409 但 detail 字段不完整`);
      }
      throw new ConflictError(
        detail.existing,
        detail.suggested_name,
        detail.message ?? "conflict",
      );
    }

    await throwIfNotOk(response, "上传失败");
    return (await response.json()) as {
      success: boolean;
      path: string;
      url: string;
      filename?: string;
      normalized?: boolean;
      original_kept?: boolean;
      original_filename?: string;
      used_encoding?: string | null;
      chapter_count?: number;
    };
  }

  /** 单文件 multipart 上传 POST，返回 JSON 响应体。 */
  private static async postFileUpload<T>(url: string, file: File): Promise<T> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { method: "POST", body: formData }));
    await throwIfNotOk(response, "上传失败");
    return (await response.json()) as T;
  }

  /** 上传分镜图或镜头视频，替换该镜头的 AI 生成资产（storyboard/grid 模式）。 */
  static async uploadShotMedia(
    projectName: string,
    scriptFile: string,
    shotId: string,
    kind: "storyboard" | "video",
    file: File
  ): Promise<ShotUploadResult> {
    const url =
      `/projects/${encodeURIComponent(projectName)}/shots/${encodeURIComponent(shotId)}` +
      `/upload/${kind}?script_file=${encodeURIComponent(scriptFile)}`;
    return API.postFileUpload<ShotUploadResult>(url, file);
  }

  // ==================== 镜头尾帧 ====================
  //
  // 三个端点同一落点：设置走上传或项目内选图两条通道，都归一为 PNG 快照写到
  // end_frames/scene_{id}.png（原地覆盖），清除删快照并置空字段。返回的
  // end_frame_image 是项目内相对路径；换图后路径不变，靠资产指纹 cache-bust。

  /** 上传任意图片作为该镜头的尾帧。 */
  static async uploadEndFrame(
    projectName: string,
    shotId: string,
    scriptFile: string,
    file: File
  ): Promise<{ success: boolean; end_frame_image: string }> {
    const url =
      `/projects/${encodeURIComponent(projectName)}/shots/${encodeURIComponent(shotId)}` +
      `/end-frame/upload?script_file=${encodeURIComponent(scriptFile)}`;
    return API.postFileUpload<{ success: boolean; end_frame_image: string }>(url, file);
  }

  /** 选项目内已有图片作为该镜头的尾帧（快照复制，不建立引用）。 */
  static async selectEndFrame(
    projectName: string,
    shotId: string,
    scriptFile: string,
    sourcePath: string
  ): Promise<{ success: boolean; end_frame_image: string }> {
    const url =
      `/projects/${encodeURIComponent(projectName)}/shots/${encodeURIComponent(shotId)}` +
      `/end-frame/select`;
    return this.request(url, {
      method: "POST",
      body: JSON.stringify({ script_file: scriptFile, source_path: sourcePath }),
    });
  }

  /** 清除该镜头的尾帧。 */
  static async clearEndFrame(
    projectName: string,
    shotId: string,
    scriptFile: string
  ): Promise<SuccessResponse> {
    const url =
      `/projects/${encodeURIComponent(projectName)}/shots/${encodeURIComponent(shotId)}` +
      `/end-frame?script_file=${encodeURIComponent(scriptFile)}`;
    return this.request(url, { method: "DELETE" });
  }

  /** 上传参考生视频单元的成片视频。 */
  static async uploadReferenceUnitVideo(
    projectName: string,
    episode: number,
    unitId: string,
    file: File
  ): Promise<ShotUploadResult> {
    const url =
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}` +
      `/units/${encodeURIComponent(unitId)}/upload-video`;
    return API.postFileUpload<ShotUploadResult>(url, file);
  }

  static async listFiles(
    projectName: string
  ): Promise<{
    files: {
      source?: { name: string; size: number; url: string; raw_filename?: string | null }[];
      characters?: { name: string; size: number; url: string }[];
      scenes?: { name: string; size: number; url: string }[];
      props?: { name: string; size: number; url: string }[];
      storyboards?: { name: string; size: number; url: string }[];
      videos?: { name: string; size: number; url: string }[];
      output?: { name: string; size: number; url: string }[];
    };
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/files`
    );
  }

  static getFileUrl(
    projectName: string,
    path: string,
    cacheBust?: number | string | null
  ): string {
    // 引导演示的占位图是现算的内联 SVG（data: URI），直接用，不要再包一层项目路径。
    // 只放行 data: —— 目前没有第二种自带协议的图源，多放行的协议只是没人用的入口。
    if (path.startsWith("data:")) {
      return path;
    }
    const base = `${API_BASE}/files/${encodeURIComponent(projectName)}/${path}`;
    if (cacheBust == null || cacheBust === "") {
      return base;
    }

    return `${base}?v=${encodeURIComponent(String(cacheBust))}`;
  }

  // ==================== Source 文件管理 ====================

  /**
   * 获取 source 文件内容
   */
  static async getSourceContent(
    projectName: string,
    filename: string
  ): Promise<string> {
    const url = `/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url)
    );
    await throwIfNotOk(response, "获取文件内容失败");
    return response.text();
  }

  /**
   * 保存 source 文件（新建或更新）
   */
  static async saveSourceFile(
    projectName: string,
    filename: string,
    content: string
  ): Promise<SuccessResponse> {
    const url = `/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, {
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: content,
      })
    );
    await throwIfNotOk(response, "保存文件失败");
    return response.json() as Promise<SuccessResponse>;
  }

  /**
   * 删除 source 文件
   */
  static async deleteSourceFile(
    projectName: string,
    filename: string
  ): Promise<SuccessResponse> {
    const url = `/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, {
        method: "DELETE",
      })
    );
    await throwIfNotOk(response, "删除文件失败");
    return response.json() as Promise<SuccessResponse>;
  }

  /**
   * 删除角色参考音频样本（清空字段并移除文件）
   */
  static async deleteCharacterReferenceAudio(
    projectName: string,
    characterName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(characterName)}/reference-audio`,
      { method: "DELETE" }
    );
  }

  // ==================== 草稿文件管理 ====================

  /**
   * 获取草稿内容
   */
  static async getDraftContent(
    projectName: string,
    episode: number,
    stepNum: number
  ): Promise<string> {
    const url = `/projects/${encodeURIComponent(projectName)}/drafts/${episode}/step${stepNum}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url)
    );
    await throwIfNotOk(response, "获取草稿内容失败");
    return response.text();
  }

  /**
   * 保存草稿内容
   */
  static async saveDraft(
    projectName: string,
    episode: number,
    stepNum: number,
    content: string
  ): Promise<SuccessResponse> {
    const url = `/projects/${encodeURIComponent(projectName)}/drafts/${episode}/step${stepNum}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, {
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: content,
      })
    );
    await throwIfNotOk(response, "保存草稿失败");
    return response.json() as Promise<SuccessResponse>;
  }

  /**
   * 删除草稿
   */
  static async deleteDraft(
    projectName: string,
    episode: number,
    stepNum: number
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/drafts/${episode}/step${stepNum}`,
      { method: "DELETE" }
    );
  }

  // ==================== 项目概述管理 ====================

  /**
   * 使用 AI 生成项目概述
   */
  static async generateOverview(
    projectName: string
  ): Promise<{ success: boolean; overview: ProjectOverview }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate-overview`,
      {
        method: "POST",
      }
    );
  }

  /**
   * 更新项目概述（手动编辑）
   */
  static async updateOverview(
    projectName: string,
    updates: Partial<ProjectOverview>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/overview`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  // ==================== 生成 API ====================

  /**
   * 生成分镜图
   * @param projectName - 项目名称
   * @param segmentId - 片段/场景 ID
   * @param prompt - 图片生成 prompt（支持字符串或结构化对象）
   * @param scriptFile - 剧本文件名
   */
  static async generateStoryboard(
    projectName: string,
    segmentId: string,
    prompt: string | Record<string, unknown>,
    scriptFile: string
  ): Promise<{ success: boolean; task_id: string; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/storyboard/${encodeURIComponent(segmentId)}`,
      {
        method: "POST",
        body: JSON.stringify({ prompt, script_file: scriptFile }),
      }
    );
  }

  /**
   * 生成视频
   * @param projectName - 项目名称
   * @param segmentId - 片段/场景 ID
   * @param prompt - 视频生成 prompt（支持字符串或结构化对象）
   * @param scriptFile - 剧本文件名
   * @param durationSeconds - 时长（秒）
   */
  static async generateVideo(
    projectName: string,
    segmentId: string,
    prompt: string | Record<string, unknown>,
    scriptFile: string,
    durationSeconds: number = 4,
    requestOptions: ReferenceGenerationRequestOptions = {},
  ): Promise<{ success: boolean; task_id: string; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/video/${encodeURIComponent(segmentId)}`,
      {
        method: "POST",
        body: JSON.stringify({
          prompt,
          script_file: scriptFile,
          duration_seconds: durationSeconds,
          ...requestOptions,
        }),
      }
    );
  }

  /**
   * 生成单段旁白配音（文本由后端从剧本 novel_text 读取）
   * @param projectName - 项目名称
   * @param segmentId - 片段 ID
   * @param scriptFile - 剧本文件名
   */
  static async generateNarrationAudio(
    projectName: string,
    segmentId: string,
    scriptFile: string
  ): Promise<{ success: boolean; task_id: string; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/tts/${encodeURIComponent(segmentId)}`,
      {
        method: "POST",
        body: JSON.stringify({ script_file: scriptFile }),
      }
    );
  }

  /**
   * 批量生成全集旁白配音（只入队缺少旁白且有原文的段）
   * @param projectName - 项目名称
   * @param scriptFile - 剧本文件名
   */
  static async generateEpisodeNarrationAudio(
    projectName: string,
    scriptFile: string
  ): Promise<{ success: boolean; task_ids: string[]; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/tts`,
      {
        method: "POST",
        body: JSON.stringify({ script_file: scriptFile }),
      }
    );
  }

  /**
   * 读取当前项目实际生效的 audio backend 音色枚举，供 TTS 试听弹窗选择音色。
   * configured=false 表示未配置任何 audio 供应商，前端据此禁用生成入口。
   * @param projectName - 项目名称
   */
  static async getAudioBackendVoices(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<{
    configured: boolean;
    provider_id: string | null;
    model: string | null;
    voices: { id: string; label: string }[];
  }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/audio-backend/voices`, {
      signal: options.signal,
    });
  }

  /**
   * 提交角色 TTS 试听样本生成任务（预览用，需再调用 confirmCharacterVoiceSample 才落资产）
   * @param projectName - 项目名称
   * @param charName - 角色名称
   * @param text - 待合成文本
   * @param voice - 音色 id
   */
  static async generateCharacterVoiceSample(
    projectName: string,
    charName: string,
    text: string,
    voice: string
  ): Promise<{ success: boolean; task_id: string; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(charName)}/voice-sample`,
      {
        method: "POST",
        body: JSON.stringify({ text, voice }),
      }
    );
  }

  /**
   * 把已生成、已试听的 TTS 样本提升为角色 reference_audio
   * @param projectName - 项目名称
   * @param charName - 角色名称
   * @param taskId - generateCharacterVoiceSample 返回的 task_id
   */
  static async confirmCharacterVoiceSample(
    projectName: string,
    charName: string,
    taskId: string
  ): Promise<{ success: boolean; path: string; url: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(charName)}/voice-sample/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ task_id: taskId }),
      }
    );
  }

  /**
   * 生成角色设计图
   * @param projectName - 项目名称
   * @param charName - 角色名称
   * @param prompt - 角色描述 prompt
   */
  static async generateCharacter(
    projectName: string,
    charName: string,
    prompt: string
  ): Promise<{
    success: boolean;
    task_id: string;
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/character/${encodeURIComponent(charName)}`,
      {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }
    );
  }

  /**
   * 生成场景设计图
   * @param projectName - 项目名称
   * @param sceneName - 场景名称
   * @param prompt - 场景描述 prompt
   */
  static async generateProjectScene(
    projectName: string,
    sceneName: string,
    prompt: string
  ): Promise<{
    success: boolean;
    task_id: string;
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/scene/${encodeURIComponent(sceneName)}`,
      {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }
    );
  }

  /**
   * 生成道具设计图
   * @param projectName - 项目名称
   * @param propName - 道具名称
   * @param prompt - 道具描述 prompt
   */
  static async generateProjectProp(
    projectName: string,
    propName: string,
    prompt: string
  ): Promise<{
    success: boolean;
    task_id: string;
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/prop/${encodeURIComponent(propName)}`,
      {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }
    );
  }

  /**
   * 生成产品标准参考图（product sheet）
   * @param projectName - 项目名称
   * @param productName - 产品名称
   * @param prompt - 产品描述 prompt
   */
  static async generateProjectProduct(
    projectName: string,
    productName: string,
    prompt: string
  ): Promise<{
    success: boolean;
    task_id: string;
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/product/${encodeURIComponent(productName)}`,
      {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }
    );
  }

  /**
   * 提交图片指令式编辑任务：以当前图为唯一底图、指令为唯一 prompt 走 i2i，
   * 新图覆盖 current、旧图进版本历史。分镜（resourceType="storyboard"）须带 scriptFile。
   */
  static async editImage(
    projectName: string,
    params: {
      resourceType: "character" | "scene" | "prop" | "product" | "storyboard";
      resourceId: string;
      instruction: string;
      scriptFile?: string | null;
    }
  ): Promise<{
    success: boolean;
    task_id: string;
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/edit/image`,
      {
        method: "POST",
        body: JSON.stringify({
          resource_type: params.resourceType,
          resource_id: params.resourceId,
          instruction: params.instruction,
          script_file: params.scriptFile ?? null,
        }),
      }
    );
  }

  // ==================== 任务队列 API ====================

  static async getTask(taskId: string): Promise<TaskItem> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}`);
  }

  static async listTasks(
    filters: TaskListFilters = {}
  ): Promise<{ items: TaskItem[]; total: number; page: number; page_size: number }> {
    const params = new URLSearchParams();
    if (filters.projectName) params.append("project_name", filters.projectName);
    if (filters.status) params.append("status", filters.status);
    if (filters.taskType) params.append("task_type", filters.taskType);
    if (filters.source) params.append("source", filters.source);
    if (filters.page) params.append("page", String(filters.page));
    if (filters.pageSize) params.append("page_size", String(filters.pageSize));
    const query = params.toString();
    return this.request(`/tasks${query ? "?" + query : ""}`);
  }

  static async listProjectTasks(
    projectName: string,
    filters: Omit<TaskListFilters, "projectName"> = {}
  ): Promise<{ items: TaskItem[]; total: number; page: number; page_size: number }> {
    const params = new URLSearchParams();
    if (filters.status) params.append("status", filters.status);
    if (filters.taskType) params.append("task_type", filters.taskType);
    if (filters.source) params.append("source", filters.source);
    if (filters.page) params.append("page", String(filters.page));
    if (filters.pageSize) params.append("page_size", String(filters.pageSize));
    const query = params.toString();
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/tasks${query ? "?" + query : ""}`
    );
  }

  static async getTaskStats(
    projectName: string | null = null
  ): Promise<{ stats: TaskStats }> {
    const params = new URLSearchParams();
    if (projectName) params.append("project_name", projectName);
    const query = params.toString();
    return this.request(`/tasks/stats${query ? "?" + query : ""}`);
  }

  // ==================== 任务取消 API ====================

  static async cancelPreview(
    taskId: string
  ): Promise<{
    task: { task_id: string; task_type: string; resource_id: string; status: string };
    cascaded: { task_id: string; task_type: string; resource_id: string }[];
  }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel-preview`);
  }

  static async cancelTask(
    taskId: string
  ): Promise<{
    cancelled: TaskItem[];
    cancelling: string[];
    skipped_terminal: TaskItem[];
  }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    });
  }

  static async cancelAllPreview(
    projectName: string
  ): Promise<{ queued_count: number }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/tasks/cancel-all-preview`
    );
  }

  static async cancelAllQueued(
    projectName: string
  ): Promise<{ cancelled_count: number; skipped_running_count: number }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/tasks/cancel-all`,
      { method: "POST" }
    );
  }

  static openProjectEventStream(options: ProjectEventStreamOptions): EventSource {
    const url = withAuthQuery(
      `${API_BASE}/projects/${encodeURIComponent(options.projectName)}/events/stream`
    );
    const source = new EventSource(url);

    const parsePayload = (event: MessageEvent): unknown => {
      try {
        return JSON.parse((event.data as string) || "{}");
      } catch (err) {
        console.error("解析项目事件 SSE 数据失败:", err, event.data);
        return null;
      }
    };

    const createHandler = <T>(
      callback?: (payload: T, event: MessageEvent) => void
    ) => {
      return (event: Event) => {
        if (typeof callback !== "function") return;
        const payload = parsePayload(event as MessageEvent);
        if (payload) {
          callback(payload as T, event as MessageEvent);
        }
      };
    };

    source.addEventListener("snapshot", createHandler(options.onSnapshot));
    source.addEventListener("changes", createHandler(options.onChanges));
    source.addEventListener("project_deleted", createHandler(options.onProjectDeleted));

    source.onerror = (event: Event) => {
      if (typeof options.onError === "function") {
        options.onError(event);
      }
    };

    return source;
  }

  // ==================== 版本管理 API ====================

  /**
   * 获取资源版本列表
   * @param projectName - 项目名称
   * @param resourceType - 资源类型 (storyboards, videos, characters, scenes, props)
   * @param resourceId - 资源 ID
   */
  static async getVersions(
    projectName: string,
    resourceType: string,
    resourceId: string
  ): Promise<{
    resource_type: string;
    resource_id: string;
    current_version: number;
    versions: VersionInfo[];
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/versions/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}`
    );
  }

  /**
   * 还原到指定版本
   * @param projectName - 项目名称
   * @param resourceType - 资源类型
   * @param resourceId - 资源 ID
   * @param version - 要还原的版本号
   */
  static async restoreVersion(
    projectName: string,
    resourceType: string,
    resourceId: string,
    version: number
  ): Promise<SuccessResponse & { file_path?: string; asset_fingerprints?: Record<string, number> }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/versions/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}/restore/${version}`,
      {
        method: "POST",
      }
    );
  }

  // ==================== 风格参考图 API ====================

  /**
   * 上传风格参考图
   * @param projectName - 项目名称
   * @param file - 图片文件
   * @returns 包含 style_image, style_description, url 的结果
   */
  static async uploadStyleImage(
    projectName: string,
    file: File
  ): Promise<{
    success: boolean;
    style_image: string;
    style_description: string;
    url: string;
  }> {
    const formData = new FormData();
    formData.append("file", file);

    const url = `/projects/${encodeURIComponent(projectName)}/style-image`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, {
        method: "POST",
        body: formData,
      })
    );

    await throwIfNotOk(response, "上传失败");

    return response.json() as Promise<{ success: boolean; style_image: string; style_description: string; url: string }>;
  }

  // ==================== 助手会话 API ====================

  /** Build the project-scoped assistant base path. */
  private static assistantBase(projectName: string): string {
    return `/projects/${encodeURIComponent(projectName)}/assistant`;
  }

  static async listAssistantSessions(
    projectName: string,
    status: string | null = null,
    options: { signal?: AbortSignal } = {}
  ): Promise<{ sessions: SessionMeta[] }> {
    const params = new URLSearchParams();
    if (status) params.append("status", status);
    const query = params.toString();
    return this.request(
      `${this.assistantBase(projectName)}/sessions${query ? "?" + query : ""}`,
      { signal: options.signal }
    );
  }

  static async getAssistantSession(
    projectName: string,
    sessionId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<{ session: SessionMeta }> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}`,
      { signal: options.signal }
    );
  }

  /** 冷读会话事件日志（after 为 seq 游标，-1 表示从头）。 */
  static async listAssistantEntries(
    projectName: string,
    sessionId: string,
    after: number = -1,
    options: { signal?: AbortSignal } = {}
  ): Promise<EntriesResponse> {
    const query = after >= 0 ? `?after=${after}` : "";
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/entries${query}`,
      { signal: options.signal }
    );
  }

  static async sendAssistantMessage(
    projectName: string,
    content: string,
    sessionId?: string | null,
    images?: ImagePayload[],
    clientKey?: string
  ): Promise<{ session_id: string; status: string; entry: TimelineEntry | null }> {
    return this.request(`${this.assistantBase(projectName)}/sessions/send`, {
      method: "POST",
      body: JSON.stringify({
        content,
        session_id: sessionId || undefined,
        images: images || [],
        client_key: clientKey || undefined,
      }),
    });
  }

  /**
   * 改写会话中某条历史用户消息：服务端分叉出新会话并在其上重跑。
   *
   * `sessionId` 是被改写的原会话，响应里的 `session_id` 是承接改写的新会话。
   * 运行中的会话由端点自动中断，调用方不必先停止。
   *
   * `images` 是锚点消息的图片附件，随改写后的文本一同进入分支会话的首条输入，
   * 形态与发送端点一致。
   */
  static async rewriteAssistantMessage(
    projectName: string,
    sessionId: string,
    anchorEntryUuid: string,
    content: string,
    images?: ImagePayload[],
    clientKey?: string
  ): Promise<{ status: string; session_id: string; origin_session_id: string | null; entry: TimelineEntry | null }> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/rewrite`,
      {
        method: "POST",
        body: JSON.stringify({
          anchor_entry_uuid: anchorEntryUuid,
          content,
          images: images || [],
          client_key: clientKey || undefined,
        }),
      }
    );
  }

  static async interruptAssistantSession(
    projectName: string,
    sessionId: string
  ): Promise<SuccessResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/interrupt`,
      {
        method: "POST",
      }
    );
  }

  static async answerAssistantQuestion(
    projectName: string,
    sessionId: string,
    questionId: string,
    answers: Record<string, string>
  ): Promise<SuccessResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/questions/${encodeURIComponent(questionId)}/answer`,
      {
        method: "POST",
        body: JSON.stringify({ answers }),
      }
    );
  }

  /** entry 流 SSE URL（after 为 seq 游标；重连续传由 EventSource Last-Event-ID 承担）。 */
  static getAssistantEntriesStreamUrl(
    projectName: string,
    sessionId: string,
    after: number = -1
  ): string {
    const base = `${API_BASE}${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/entries/stream`;
    const url = after >= 0 ? `${base}?after=${after}` : base;
    return withAuthQuery(url);
  }

  static async listAssistantSkills(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<{ skills: SkillInfo[] }> {
    return this.request(
      `${this.assistantBase(projectName)}/skills`,
      { signal: options.signal }
    );
  }

  static async deleteAssistantSession(
    projectName: string,
    sessionId: string
  ): Promise<SuccessResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "DELETE",
      }
    );
  }

  // ==================== 费用统计 API ====================

  /**
   * 获取统计摘要
   * @param filters - 筛选条件
   */
  static async getUsageStats(
    filters: UsageStatsFilters = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<Record<string, unknown>> {
    const params = new URLSearchParams();
    if (filters.projectName)
      params.append("project_name", filters.projectName);
    if (filters.startDate) params.append("start_date", filters.startDate);
    if (filters.endDate) params.append("end_date", filters.endDate);
    const query = params.toString();
    return this.request(`/usage/stats${query ? "?" + query : ""}`, {
      signal: options.signal,
    });
  }

  /**
   * 获取调用记录列表
   * @param filters - 筛选条件
   */
  static async getUsageCalls(
    filters: UsageCallsFilters = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<Record<string, unknown>> {
    const params = new URLSearchParams();
    if (filters.projectName)
      params.append("project_name", filters.projectName);
    if (filters.callType) params.append("call_type", filters.callType);
    if (filters.status) params.append("status", filters.status);
    if (filters.startDate) params.append("start_date", filters.startDate);
    if (filters.endDate) params.append("end_date", filters.endDate);
    if (filters.page) params.append("page", String(filters.page));
    if (filters.pageSize) params.append("page_size", String(filters.pageSize));
    const query = params.toString();
    return this.request(`/usage/calls${query ? "?" + query : ""}`, { signal: options.signal });
  }

  /**
   * 获取有调用记录的项目列表
   */
  static async getUsageProjects(): Promise<{ projects: string[] }> {
    return this.request("/usage/projects");
  }

  // ==================== API Key 管理 API ====================

  /** 列出所有 API Key（不含完整 key）。 */
  static async listApiKeys(): Promise<ApiKeyInfo[]> {
    return this.request("/api-keys");
  }

  /** 创建新 API Key，返回含完整 key 的响应（仅此一次）。 */
  static async createApiKey(name: string, expiresDays?: number): Promise<CreateApiKeyResponse> {
    return this.request("/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, expires_days: expiresDays ?? null }),
    });
  }

  /** 删除（吊销）指定 API Key。 */
  static async deleteApiKey(keyId: number): Promise<void> {
    return this.request(`/api-keys/${keyId}`, { method: "DELETE" });
  }

  // ==================== Provider 管理 API ====================

  /** 获取所有 provider 列表及状态。 */
  static async getProviders(): Promise<{ providers: ProviderInfo[] }> {
    return this.request("/providers");
  }

  /** 获取指定 provider 的配置详情（含字段列表）。 */
  static async getProviderConfig(id: string): Promise<ProviderConfigDetail> {
    return this.request(`/providers/${encodeURIComponent(id)}/config`);
  }

  /** 更新指定 provider 的配置字段。 */
  static async patchProviderConfig(
    id: string,
    patch: Record<string, string | null>
  ): Promise<void> {
    return this.request(`/providers/${encodeURIComponent(id)}/config`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  /** 测试指定 provider 的连接。 */
  static async testProviderConnection(id: string, credentialId?: number): Promise<ProviderTestResult> {
    const params = credentialId != null ? `?credential_id=${credentialId}` : "";
    return this.request(`/providers/${encodeURIComponent(id)}/test${params}`, {
      method: "POST",
    });
  }

  // ==================== Provider 凭证管理 API ====================

  static async listCredentials(providerId: string): Promise<{ credentials: ProviderCredential[] }> {
    return this.request(`/providers/${encodeURIComponent(providerId)}/credentials`);
  }

  static async createCredential(
    providerId: string,
    data: { name: string; api_key?: string; base_url?: string; access_key?: string; secret_key?: string },
  ): Promise<ProviderCredential> {
    return this.request(`/providers/${encodeURIComponent(providerId)}/credentials`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  static async updateCredential(
    providerId: string,
    credId: number,
    data: { name?: string; api_key?: string; base_url?: string; access_key?: string; secret_key?: string },
  ): Promise<void> {
    return this.request(
      `/providers/${encodeURIComponent(providerId)}/credentials/${credId}`,
      { method: "PATCH", body: JSON.stringify(data) },
    );
  }

  static async deleteCredential(providerId: string, credId: number): Promise<void> {
    return this.request(
      `/providers/${encodeURIComponent(providerId)}/credentials/${credId}`,
      { method: "DELETE" },
    );
  }

  static async activateCredential(providerId: string, credId: number): Promise<void> {
    return this.request(
      `/providers/${encodeURIComponent(providerId)}/credentials/${credId}/activate`,
      { method: "POST" },
    );
  }

  static async uploadVertexCredential(name: string, file: File): Promise<ProviderCredential> {
    const formData = new FormData();
    formData.append("file", file);
    const url = `/providers/gemini-vertex/credentials/upload?name=${encodeURIComponent(name)}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, { method: "POST", body: formData }),
    );
    await throwIfNotOk(response, "上传凭证失败");
    return response.json() as Promise<ProviderCredential>;
  }

  // ==================== Agent 配置 / 凭证 API ====================

  static async listAgentPresetProviders(): Promise<PresetProvidersResponse> {
    return this.request("/agent/preset-providers");
  }

  static async listAgentCredentials(): Promise<{ credentials: AgentCredential[] }> {
    return this.request("/agent/credentials");
  }

  static async createAgentCredential(
    data: CreateAgentCredentialRequest,
  ): Promise<AgentCredential> {
    return this.request("/agent/credentials", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  static async updateAgentCredential(
    id: number,
    data: UpdateAgentCredentialRequest,
  ): Promise<AgentCredential> {
    return this.request(`/agent/credentials/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  static async deleteAgentCredential(id: number): Promise<void> {
    return this.request(`/agent/credentials/${id}`, { method: "DELETE" });
  }

  static async activateAgentCredential(id: number): Promise<{ active_id: number }> {
    return this.request(`/agent/credentials/${id}/activate`, { method: "POST" });
  }

  static async testAgentCredential(id: number): Promise<TestConnectionResponse> {
    return this.request(`/agent/credentials/${id}/test`, { method: "POST" });
  }

  static async testAgentConnectionDraft(
    data: TestConnectionRequest,
  ): Promise<TestConnectionResponse> {
    return this.request("/agent/test-connection", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  // ==================== 自定义供应商 API ====================

  static async listCustomProviders(): Promise<{ providers: CustomProviderInfo[] }> {
    return this.request("/custom-providers");
  }

  static async listEndpointCatalog(): Promise<{ endpoints: EndpointDescriptor[] }> {
    return this.request("/custom-providers/endpoints");
  }

  static async createCustomProvider(data: CustomProviderCreateRequest): Promise<CustomProviderInfo> {
    return this.request("/custom-providers", { method: "POST", body: JSON.stringify(data) });
  }

  static async getCustomProvider(id: number): Promise<CustomProviderInfo> {
    return this.request(`/custom-providers/${id}`);
  }

  static async updateCustomProvider(id: number, data: Partial<Omit<CustomProviderCreateRequest, "discovery_format" | "models" | "image_max_workers" | "video_max_workers" | "audio_max_workers">>): Promise<void> {
    return this.request(`/custom-providers/${id}`, { method: "PATCH", body: JSON.stringify(data) });
  }

  static async fullUpdateCustomProvider(id: number, data: CustomProviderFullUpdateRequest): Promise<CustomProviderInfo> {
    return this.request(`/custom-providers/${id}`, { method: "PUT", body: JSON.stringify(data) });
  }

  static async deleteCustomProvider(id: number): Promise<void> {
    return this.request(`/custom-providers/${id}`, { method: "DELETE" });
  }

  static async replaceCustomProviderModels(id: number, models: CustomProviderModelInput[]): Promise<CustomProviderModelInfo[]> {
    return this.request(`/custom-providers/${id}/models`, { method: "PUT", body: JSON.stringify({ models }) });
  }

  static async discoverModels(data: { discovery_format: string; base_url: string; api_key: string }): Promise<{ models: DiscoveredModel[] }> {
    return this.request("/custom-providers/discover", { method: "POST", body: JSON.stringify(data) });
  }

  static async discoverModelsForProvider(id: number): Promise<{ models: DiscoveredModel[] }> {
    return this.request(`/custom-providers/${id}/discover`, { method: "POST" });
  }

  static async testCustomConnection(data: { discovery_format: string; base_url: string; api_key: string }): Promise<{ success: boolean; message: string }> {
    return this.request("/custom-providers/test", { method: "POST", body: JSON.stringify(data) });
  }

  static async testCustomConnectionById(id: number): Promise<{ success: boolean; message: string }> {
    return this.request(`/custom-providers/${id}/test`, { method: "POST" });
  }

  static async getCustomProviderCredentials(id: number): Promise<CustomProviderCredentials> {
    return this.request(`/custom-providers/${id}/credentials`);
  }

  static async discoverAnthropicModels(
    data: AnthropicDiscoverRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<AnthropicDiscoverResponse> {
    return this.request("/custom-providers/discover-anthropic", {
      method: "POST",
      body: JSON.stringify(data),
      signal: options.signal,
    });
  }

  // ==================== 用量统计（按 provider 分组）API ====================

  /**
   * 获取按 provider 分组的用量统计。
   * @param params - 可选筛选：provider、start、end（ISO 日期字符串）
   */
  static async getUsageStatsGrouped(
    params: { provider?: string; start?: string; end?: string } = {}
  ): Promise<UsageStatsResponse> {
    const searchParams = new URLSearchParams();
    searchParams.append("group_by", "provider");
    if (params.provider) searchParams.append("provider", params.provider);
    if (params.start) searchParams.append("start_date", params.start);
    if (params.end) searchParams.append("end_date", params.end);
    return this.request(`/usage/stats?${searchParams.toString()}`);
  }

  // ==================== 费用估算 API ====================

  /**
   * 获取项目费用估算。
   * @param projectName - 项目名称
   */
  static async getCostEstimate(
    projectName: string,
    options: ReferenceRequestOptions & { referenceUnitId?: string; signal?: AbortSignal } = {}
  ): Promise<CostEstimateResponse> {
    const suffix = referenceRequestQuery(
      options,
      options.referenceUnitId ? { reference_unit_id: options.referenceUnitId } : undefined,
    );
    return this.request(`/projects/${encodeURIComponent(projectName)}/cost-estimate${suffix}`, {
      signal: options.signal,
    });
  }

  // ==================== Grid 图生视频 API ====================

  /**
   * 生成 Grid 图像（多场景网格）
   * @param projectName - 项目名称
   * @param episode - 剧集编号
   * @param scriptFile - 剧本文件名
   * @param sceneIds - 可选，指定场景 ID 列表
   */
  static async generateGrid(
    projectName: string,
    episode: number,
    scriptFile: string,
    sceneIds?: string[]
  ): Promise<{ success: boolean; grid_ids: string[]; task_ids: string[]; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/grid/${episode}`,
      { method: "POST", body: JSON.stringify({ script_file: scriptFile, scene_ids: sceneIds }) }
    );
  }

  /**
   * 列出项目所有 Grid 记录
   * @param projectName - 项目名称
   */
  static async listGrids(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<GridGeneration[]> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/grids`, {
      signal: options.signal,
    });
  }

  /**
   * 获取项目的宫格档位能力（4×4/5×5 是否可用、单张格数上限）
   * @param projectName - 项目名称
   */
  static async getGridCapability(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<GridCapability> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/grid-capability`, {
      signal: options.signal,
    });
  }

  /**
   * 获取单个 Grid 详情
   * @param projectName - 项目名称
   * @param gridId - Grid ID
   */
  static async getGrid(projectName: string, gridId: string): Promise<GridGeneration> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}`);
  }

  /**
   * 重新生成 Grid 图像
   * @param projectName - 项目名称
   * @param gridId - Grid ID
   */
  static async regenerateGrid(
    projectName: string,
    gridId: string
  ): Promise<{ success: boolean; task_id: string; deduped: boolean }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}/regenerate`,
      { method: "POST" }
    );
  }

  /**
   * 切分落格：按当前联合图覆写各分镜格（唯一覆写分镜格的操作，同步执行）
   * @param projectName - 项目名称
   * @param gridId - Grid ID
   */
  static async splitGrid(
    projectName: string,
    gridId: string
  ): Promise<{
    success: boolean;
    split_at: string | null;
    updated_scene_ids: string[];
    missing_scene_ids: string[];
    asset_fingerprints: Record<string, number>;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}/split`,
      { method: "POST" }
    );
  }

  /**
   * 上传联合图替换当前宫格图（仅转 PNG 归一化，不缩放、不校验布局；不触发切分）
   * @param projectName - 项目名称
   * @param gridId - Grid ID
   * @param file - 图片文件
   */
  static async uploadGridImage(
    projectName: string,
    gridId: string,
    file: File
  ): Promise<{ success: boolean; path: string; version: number; asset_fingerprints: Record<string, number> }> {
    const url = `/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}/upload`;
    return API.postFileUpload(url, file);
  }

  // ==================== Global Asset Library ====================

  static async listAssets(
    params: { type?: AssetType; q?: string; limit?: number; offset?: number } = {},
    options: RequestInit = {},
  ) {
    const usp = new URLSearchParams();
    if (params.type) usp.set("type", params.type);
    if (params.q) usp.set("q", params.q);
    if (params.limit) usp.set("limit", String(params.limit));
    if (params.offset) usp.set("offset", String(params.offset));
    return this.request<{ items: Asset[] }>(`/assets?${usp.toString()}`, options);
  }

  static async getAsset(id: string) {
    return this.request<{ asset: Asset }>(`/assets/${encodeURIComponent(id)}`);
  }

  static async createAsset(payload: AssetCreatePayload & { image?: File }) {
    const form = new FormData();
    form.append("type", payload.type);
    form.append("name", payload.name);
    form.append("description", payload.description ?? "");
    form.append("voice_style", payload.voice_style ?? "");
    if (payload.image) form.append("image", payload.image);
    const endpoint = "/assets";
    const url = `${API_BASE}${endpoint}`;
    const response = await fetch(url, withAuth(endpoint, { method: "POST", body: form }));
    if (!response.ok) {
      handleUnauthorized(response);
      const error = (await response.json().catch(() => ({ detail: response.statusText }))) as {
        detail?: string;
      };
      throw new Error(typeof error.detail === "string" ? error.detail : "请求失败");
    }
    return response.json() as Promise<{ asset: Asset }>;
  }

  static async updateAsset(id: string, patch: AssetUpdatePayload) {
    return this.request<{ asset: Asset }>(`/assets/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  static async replaceAssetImage(id: string, image: File) {
    const form = new FormData();
    form.append("image", image);
    const endpoint = `/assets/${encodeURIComponent(id)}/image`;
    const url = `${API_BASE}${endpoint}`;
    const response = await fetch(url, withAuth(endpoint, { method: "POST", body: form }));
    if (!response.ok) {
      handleUnauthorized(response);
      const error = (await response.json().catch(() => ({ detail: response.statusText }))) as {
        detail?: string;
      };
      throw new Error(typeof error.detail === "string" ? error.detail : "请求失败");
    }
    return response.json() as Promise<{ asset: Asset }>;
  }

  static async deleteAsset(id: string): Promise<void> {
    return this.request(`/assets/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  static async addAssetFromProject(payload: {
    project_name: string;
    resource_type: AssetType;
    resource_id: string;
    override_name?: string;
    overwrite?: boolean;
  }) {
    return this.request<{ asset: Asset }>(`/assets/from-project`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static async applyAssetsToProject(payload: {
    asset_ids: string[];
    target_project: string;
    conflict_policy: "skip" | "overwrite" | "rename";
  }) {
    return this.request<{
      succeeded: Array<{ id: string; name: string }>;
      skipped: Array<{ id: string; name: string }>;
      failed: Array<{ id: string; reason: string }>;
    }>(`/assets/apply-to-project`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static getGlobalAssetUrl(path: string | null, fp?: string | null): string | null {
    if (!path) return null;
    const parts = path.split("/");
    if (parts.length < 3 || parts[0] !== "_global_assets") return null;
    const type = parts[1];
    const filename = parts.slice(2).join("/");
    const qs = fp ? `?fp=${encodeURIComponent(fp)}` : "";
    return `${API_BASE}/global-assets/${type}/${filename}${qs}`;
  }

  // ==================== Reference-to-Video API ====================

  /** List reference-video units for an episode. */
  static async listReferenceVideoUnits(
    projectName: string,
    episode: number,
  ): Promise<{ units: ReferenceVideoUnit[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
    );
  }

  /** Create a new reference-video unit. */
  static async addReferenceVideoUnit(
    projectName: string,
    episode: number,
    payload: {
      prompt: string;
      references: ReferenceResource[];
      duration_seconds?: number;
      transition_to_next?: TransitionType;
      note?: string | null;
    },
  ): Promise<{ unit: ReferenceVideoUnit }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

  /** Patch prompt/references/duration/transition/note on an existing unit. */
  static async patchReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
    patch: {
      prompt?: string;
      references?: ReferenceResource[];
      duration_seconds?: number;
      transition_to_next?: TransitionType;
      note?: string | null;
    },
  ): Promise<{ unit: ReferenceVideoUnit }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    );
  }

  /** Delete a unit. Returns void on 204. */
  static async deleteReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
  ): Promise<void> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}`,
      { method: "DELETE" },
    );
  }

  /** Reorder units by providing the full ordered unit_id list. */
  static async reorderReferenceVideoUnits(
    projectName: string,
    episode: number,
    unitIds: string[],
  ): Promise<{ units: ReferenceVideoUnit[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/reorder`,
      { method: "POST", body: JSON.stringify({ unit_ids: unitIds }) },
    );
  }

  /**
   * 入队前的时长取档预检：申请秒数与请求时长基准不一致时需先向用户确认。
   *
   * 预检按请求时的项目、剧本与资产状态解析；worker 启动时重新投影当前状态。
   */
  static async precheckReferenceVideoDuration(
    projectName: string,
    episode: number,
    unitId: string,
    options?: ReferenceRequestOptions & { signal?: AbortSignal },
  ): Promise<ReferenceDurationPrecheck> {
    const suffix = referenceRequestQuery(options ?? {});
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}/duration-precheck${suffix}`,
      { signal: options?.signal },
    );
  }

  /**
   * 分镜文稿的读时派生预览：shots / references / utterances + 降级可见性提示。
   *
   * 只读、不落盘——文稿是唯一真相。提示文本由后端按请求语言渲染（含依赖项目当前
   * 视频模型能力的声音相关几条），前端不再二次翻译。
   */
  static async previewReferenceScript(
    projectName: string,
    episode: number,
    prompt: string,
    options?: { signal?: AbortSignal },
  ): Promise<ScriptPreview> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/script-preview`,
      { method: "POST", body: JSON.stringify({ prompt }), signal: options?.signal },
    );
  }

  /** Enqueue generation; returns 202 with task_id. */
  static async generateReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
    options: ReferenceGenerationRequestOptions = {},
  ): Promise<{ task_id: string; deduped: boolean }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}/generate`,
      { method: "POST", body: JSON.stringify(options) },
    );
  }

}

export { API };
