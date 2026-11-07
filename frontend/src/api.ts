/**
 * API request wrapper (TypeScript)
 *
 * Typed API layer for all backend endpoints.
 * Import: import { API } from '@/api';
 */

import type {
  CharacterDerivativeStatus,
  ProjectData,
  ProjectSummary,
  ImportConflictPolicy,
  ImportProjectResponse,
  ExportDiagnostics,
  FinalCutStatus,
  JianyingDraftStatus,
  JianyingVersion,
  RenderOptions,
  RenderSubmission,
  SubtitleMode,
  ImportFailureDiagnostics,
  EpisodeScript,
  TaskItem,
  TaskStats,
  SessionMeta,
  ImagePayload,
  EntriesResponse,
  AcceptedMessageResponse,
  QueuedMessageWithdrawal,
  QueuedMessageResendResponse,
  QueuedMessageWithdrawalResponse,
  QueuedMessageSendNowResponse,
  SkillInfo,
  ProjectOverview,
  ProjectChangeBatchPayload,
  ProjectEventSnapshotPayload,
  ProjectDeletedPayload,
  AssistantSessionResumedPayload,
  AutoBackendsResponse,
  GetSystemConfigResponse,
  GetSystemVersionResponse,
  NarrationDefaultsResponse,
  TtsModelCapabilitiesResponse,
  PromptTemplateDetail,
  PromptTemplateListResponse,
  PromptTemplatePartial,
  ModelCandidatesResponse,
  OnboardingStatus,
  SystemConfigPatch,
  ApiKeyInfo,
  AssetLibraryAccount,
  AssetLibraryAccountList,
  AssetLibraryBindableCredential,
  AssetLibraryBindingStatus,
  AssetLibraryCredentialType,
  CreateAssetLibraryAccountPayload,
  NewImageTarget,
  ObjectStorageConnection,
  ObjectStorageSettings,
  ProviderAssetImportResult,
  ProviderAssetLink,
  ProviderAssetLinkPanel,
  ProviderAssetPage,
  SaveObjectStorageConnectionPayload,
  UpdateAssetLibraryAccountPayload,
  CreateApiKeyResponse,
  ProviderInfo,
  CatalogEntryHit,
  CatalogSearchResponse,
  NewCatalogEntry,
  NewEntryHints,
  MediaType,
  ProviderConfigDetail,
  ConnectivityCheckResult,
  ProviderCredential,
  UsageAgentSessionDetail,
  UsageAgentSessionPage,
  UsageRecordDetail,
  UsageRecordPage,
  UsageSummary,
  CustomProviderInfo,
  CustomProviderCreateRequest,
  CustomProviderFullUpdateRequest,
  ProviderMultiplierInfo,
  ProviderModelItem,
  ProviderModelsResponse,
  FetchProviderModelsResponse,
  AddProviderModelRequest,
  AddProviderModelResponse,
  ProviderModelDetail,
  ProviderModelReferences,
  ProviderModelSettingsUpdate,
  CatalogEntryKey,
  DiscoverModelsResponse,
  EndpointDescriptor,
  ComfyuiInferResponse,
  ComfyuiMediaType,
  CustomEndpointInfo,
  MarketEntryAggregate,
  MarketSubmission,
  MarketSubmissionDiagnostic,
  MarketSubmissionDraft,
  MarketEntryListResponse,
  MarketEntryDetail,
  MarketEntryInstallation,
  OfficialServiceState,
  MarketSourceInfo,
  MarketSourceListResponse,
  EndpointDefinition,
  EndpointValidateResponse,
  EndpointTestParameters,
  EndpointTestCredentials,
  EndpointTestAssets,
  EndpointPreviewResponse,
  EndpointStageReport,
  EndpointTestStage,
  TrialRunInfo,
  TrialRunModelRef,
  AnthropicDiscoverRequest,
  AnthropicDiscoverResponse,
  CostEstimateResponse,
  ReferenceUnitCapability,
  ReferenceUnitCapabilityMap,
  ReferenceVideoUnit,
  AdShot,
  DramaScene,
  NarrationSegment,
  ReferenceDurationPrecheck,
  ReferenceGenerationRequestOptions,
  ReferenceBatchAdmission,
  ReferenceBatchGenerateRequest,
  ReferenceBatchPreview,
  ScriptPreview,
  ReferenceUnitPromptPreview,
  ItemPromptPreview,
  RenderedPromptPreview,
  ScriptReviewState,
  DraftDocType,
  EpisodeDraftSummary,
  EpisodeDraftView,
  SaveEpisodeDraftResult,
  AuthorPromptsRequest,
  AuthorPromptsResponse,
  PlanScriptRequest,
  PlanScriptResponse,
  GenerateAdScriptRequest,
  DraftRepairResponse,
  DramaNormalizedScript,
  NarrationScriptPlanDraft,
  ReferenceScriptPlanDraft,
  VideoCapabilities,
  EpisodeItemRef,
} from "@/types";
import type { GridCapability, GridGeneration } from "@/types/grid";
import type {
  PresentationReadModel,
  PresentationRequestOptions,
  PresentationResourceType,
} from "@/types/presentation";
import type { Asset, AssetType, AssetCreatePayload, AssetListPage, AssetUpdatePayload } from "@/types/asset";
import type { AgentMemoryOverview, AgentMemoryScope } from "@/types/agent-memory";
import type {
  DeletedProjectListing,
  FileViewListing,
  RestoreResult,
  TrashListing,
  TrashRecord,
} from "@/types/file-view";
import type {
  EpisodeNextStep,
  WorkflowMigrationReport,
  WorkflowPlan,
  WorkflowPlanRequest,
  WorkflowStatus,
} from "@/types/workflow";
import type {
  EditTimelinePreviewMedia,
  EditTimelineReadout,
  EditTimelineSummary,
  EpisodeEditOverview,
  ProjectBgm,
  TimelineNarration,
} from "@/types/edit-timeline";
import type {
  AdoptSourceFileTarget,
  CreateEpisodeBody,
  EpisodeDeletionResponse,
  EpisodePlanningResponse,
  PlanningGap,
  EpisodeSourceWriteResult,
  EpisodesView,
  ManualSplitAction,
  ManualSplitResponse,
  ReplanAdoptionResponse,
  ReplanPreview,
  StopEpisodePlanningResponse,
  SourceFileChangeResponse,
  SourceKind,
  SourceKindChangeResult,
} from "@/types/episodes-view";
import type {
  AssetRegenerationImpact,
  AssetSheetBatchPreview,
  AssetSheetBatchScope,
  AssetSheetBatchSubmitted,
  AssetSheetStatusRow,
  AssetSheetType,
} from "@/types/asset-sheet";
import type {
  StoryboardBatchKind,
  StoryboardBatchPreview,
  StoryboardBatchSubmitted,
} from "@/types/storyboard-batch";
import type {
  AgentCredential,
  AgentCredentialTier,
  CreateAgentCredentialRequest,
  PresetProvidersResponse,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateAgentCredentialRequest,
} from "@/types/agent-credential";
import i18n from "@/i18n";
import { openSseStream, type SseStreamHandle } from "@/utils/sse-stream";
import {
  API_BASE,
  handleUnauthorized,
  parseSseJson,
  requestJson,
  sseErrorHandler,
  sseHeaders,
  throwIfNotOk,
  withAuth,
} from "./api/transport";
import {
  ConflictError,
  type ImportErrorPayload,
  type ScriptEditResult,
} from "./api/errors";
import type {
  AgentProfileStatus,
  AssetCreateResult,
  AssetDeletionPreview,
  AssetMergeResult,
  AssetRenameResult,
  AssistantEntriesStreamOptions,
  CreateProjectPayload,
  EpisodeScriptSnapshot,
  MergeableAssetType,
  ProjectAssetType,
  ProjectEventStreamOptions,
  ScriptEditCommand,
  ShotUploadResult,
  SuccessResponse,
  TaskListFilters,
  UsageRecordsQuery,
  UsageSessionsQuery,
  UsageSummaryQuery,
  VersionInfo,
  VideoCapabilitiesQuery,
} from "./api/types";

export {
  AgentFailureError,
  ApiRequestError,
  ConflictError,
  ReadOnlyModeError,
  ReferenceProjectionError,
  ScriptEditCommandError,
  SpeechAdmissionError,
  type ErrorResponse,
} from "./api/errors";
export type {
  AgentProfileStatus,
  AssetCreateResult,
  AssetDeletionPreview,
  AssetMergeEpisodeImpact,
  AssetMergeResult,
  AssetRenameResult,
  LoginResponse,
  MergeableAssetType,
  ProjectAssetType,
  ProjectEventStreamOptions,
  UsageRecordsQuery,
  UsageSessionsQuery,
  UsageSummaryQuery,
  VersionInfo,
  VideoCapabilitiesQuery,
} from "./api/types";
export { setApiReadOnly } from "./api/transport";

// ==================== Endpoint helpers ====================

/** Available project-file thumbnail widths, matching server `?w=` tiers; other widths round up to a server tier. */
export type FileUrlWidth = 160 | 320 | 640 | 1280;

export interface FileUrlOptions {
  /** Target width for server thumbnails (WebP, preserving aspect ratio); omission requests the original image. */
  width?: FileUrlWidth;
}

/** asset_type → REST path segment, matching backend spec.subdir. */
const ASSET_TYPE_PATH: Record<ProjectAssetType, string> = {
  character: "characters",
  scene: "scenes",
  prop: "props",
  product: "products",
};

/** Resource type for character-derivative images in version/image-edit endpoints, matching backend `lib/project/resource_paths`. */
export const CHARACTER_DERIVATIVE_RESOURCE_TYPE = "character_derivatives";

/** Derivative composite resource ID: base and derivative names occupy separate segments, matching backend storage, queue and version semantics. */
export function derivativeResourceId(characterName: string, derivativeName: string): string {
  return `${characterName}/${derivativeName}`;
}

/** Path-parameter values that may contain `/`, such as model IDs: encode each segment while preserving separators, matching server `{name:path}`. */
function encodePath(value: string): string {
  return value.split("/").map(encodeURIComponent).join("/");
}

/** Character-derivative subresource prefix: derivatives belong under character entries, with each name encoded separately. */
function derivativesPath(projectName: string, charName: string): string {
  return `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(charName)}/derivatives`;
}

/**
 * Version-endpoint resource prefix. Character-derivative resource IDs use `本体/衍生`, which cannot fit a generic single-segment path parameter,
 * so the backend provides a separate two-segment route. Dispatch by resource type here; callers still pass one resource ID.
 */
function versionsResourcePath(projectName: string, resourceType: string, resourceId: string): string {
  const base = `/projects/${encodeURIComponent(projectName)}/versions`;
  if (resourceType === CHARACTER_DERIVATIVE_RESOURCE_TYPE) {
    const [owner = "", derivative = ""] = resourceId.split("/");
    return `${base}/character-derivative/${encodeURIComponent(owner)}/${encodeURIComponent(derivative)}`;
  }
  return `${base}/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}`;
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

function endpointTestRequest(
  body: unknown,
  assets: EndpointTestAssets = {},
  signal?: AbortSignal,
): RequestInit {
  const files = Object.entries(assets).flatMap(([source, items]) =>
    (items ?? []).map((file) => [source, file] as const),
  );
  if (files.length === 0) return { method: "POST", body: JSON.stringify(body), signal };
  const form = new FormData();
  form.append("payload", JSON.stringify(body));
  for (const [source, file] of files) form.append(source, file);
  return { method: "POST", headers: {}, body: form, signal };
}

function videoCapabilitiesQuery(options: VideoCapabilitiesQuery): string {
  const params = new URLSearchParams();
  if (options.videoBackend) params.set("video_backend", options.videoBackend);
  if (options.resolution !== undefined) params.set("resolution", options.resolution ?? "");
  if (options.usesReferenceImages !== undefined) {
    params.set("uses_reference_images", String(options.usesReferenceImages));
  }
  return params.size > 0 ? `?${params.toString()}` : "";
}

/** Memory API prefix: user memory omits user_id, which the server derives from the current authenticated user. */
function agentMemoryBase(scope: AgentMemoryScope): string {
  return scope.level === "user"
    ? "/agent/memory"
    : `/projects/${encodeURIComponent(scope.projectName)}/agent-memory`;
}

// ==================== API class ====================

class API {
  /**
   * Generic request method
   */
  static async request<T = unknown>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    return requestJson<T>(endpoint, options);
  }

  // ==================== System configuration ====================

  static async getSystemConfig(options: { signal?: AbortSignal } = {}): Promise<GetSystemConfigResponse> {
    return this.request("/system/config", { signal: options.signal });
  }

  /**
   * Task-type bucket dropdown candidates (docs/adr/0054): all default-layer models plus capability-filtered models per bucket.
   * Match getSystemConfig options, including hidden-model exclusion; add filtering only at the bucket layer.
   */
  static async getModelCandidates(
    options: { signal?: AbortSignal } = {}
  ): Promise<ModelCandidatesResponse> {
    return this.request("/system/config/model-candidates", { signal: options.signal });
  }

  /** Actual models for automatic selection: backend choices for each purpose when every layer is empty; refetch after provider-configuration changes. */
  static async getAutoBackends(options: { signal?: AbortSignal } = {}): Promise<AutoBackendsResponse> {
    return this.request("/system/config/auto-backends", { signal: options.signal });
  }

  /** Prefill values for new TTS projects: global default TTS model, voice and speech rate. */
  static async getNarrationDefaults(
    options: { signal?: AbortSignal } = {},
  ): Promise<NarrationDefaultsResponse> {
    return this.request("/system/narration-defaults", { signal: options.signal });
  }

  /** Selected TTS model capabilities (provider/model): speech-rate support and the voice list from its endpoint. */
  static async getTtsModelCapabilities(
    backend: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<TtsModelCapabilitiesResponse> {
    return this.request(`/system/tts-model-capabilities?backend=${encodeURIComponent(backend)}`, {
      signal: options.signal,
    });
  }

  static async getSystemVersion(): Promise<GetSystemVersionResponse> {
    return this.request("/system/version");
  }

  // ==================== Prompt templates ====================

  static async listPromptTemplates(
    options: { signal?: AbortSignal } = {}
  ): Promise<PromptTemplateListResponse> {
    return this.request("/prompt-templates", { signal: options.signal });
  }

  /** Template IDs contain `/` hierarchy; encode each segment while preserving separators. */
  static async getPromptTemplate(
    templateId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<PromptTemplateDetail> {
    const path = templateId.split("/").map(encodeURIComponent).join("/");
    return this.request(`/prompt-templates/${path}`, { signal: options.signal });
  }

  /** Partial names likewise use `/` hierarchy and the same encoding as template IDs. */
  static async getPromptPartial(
    name: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<PromptTemplatePartial> {
    const path = name.split("/").map(encodeURIComponent).join("/");
    return this.request(`/prompt-templates/partials/${path}`, { signal: options.signal });
  }

  // ==================== First-use onboarding ====================

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

  // ==================== Project management ====================

  static async listProjects(options: { signal?: AbortSignal } = {}): Promise<{ projects: ProjectSummary[] }> {
    return this.request("/projects", { signal: options.signal });
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
    if ("content_mode" in updates) {
      throw new Error(i18n.t("errors:content_mode_immutable"));
    }
    return this.request(`/projects/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    });
  }

  static async getAgentProfileStatus(
    name: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<AgentProfileStatus> {
    return this.request(`/projects/${encodeURIComponent(name)}/agent-profile`, { signal: options.signal });
  }

  static async resetAgentProfile(name: string): Promise<AgentProfileStatus> {
    return this.request(`/projects/${encodeURIComponent(name)}/agent-profile/reset`, {
      method: "POST",
    });
  }

  // ==================== Agent memory ====================

  // ==================== File view ====================

  /** List immediate children of a project directory, with empty `path` meaning the project root; expand large directories level by level as needed. */
  static async getFileView(
    projectName: string,
    path: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<FileViewListing> {
    const query = new URLSearchParams({ path }).toString();
    return this.request(`/projects/${encodeURIComponent(projectName)}/file-view?${query}`, { signal: options.signal });
  }

  /** Read raw file content for preview/download through authenticated endpoints, returning Blob instead of a directly openable URL. */
  static async getFileViewContent(
    projectName: string,
    path: string,
    options: { download?: boolean; signal?: AbortSignal } = {}
  ): Promise<Blob> {
    const query = new URLSearchParams({ path, ...(options.download ? { download: "true" } : {}) }).toString();
    const url = `/projects/${encodeURIComponent(projectName)}/file-view/content?${query}`;
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { signal: options.signal }));
    await throwIfNotOk(response, i18n.t("errors:file_view_load_failed"));
    return response.blob();
  }

  /** Move an unreferenced file into project trash; the server rejects other file roles. */
  static async deleteFileViewEntry(projectName: string, path: string): Promise<{ record: TrashRecord }> {
    const query = new URLSearchParams({ path }).toString();
    return this.request(`/projects/${encodeURIComponent(projectName)}/file-view?${query}`, { method: "DELETE" });
  }

  // ==================== Project trash ====================

  static async getTrash(projectName: string, options: { signal?: AbortSignal } = {}): Promise<TrashListing> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/trash`, { signal: options.signal });
  }

  static async restoreTrashRecord(projectName: string, recordId: string): Promise<RestoreResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/trash/${encodeURIComponent(recordId)}/restore`,
      { method: "POST" },
    );
  }

  /** Download a deleted file from its deletion record, including structural files only downloadable after degraded restoration. */
  static async downloadTrashRecordFile(projectName: string, recordId: string, path: string): Promise<Blob> {
    const query = new URLSearchParams({ path }).toString();
    const url = `/projects/${encodeURIComponent(projectName)}/trash/${encodeURIComponent(recordId)}/files?${query}`;
    const response = await fetch(`${API_BASE}${url}`, withAuth(url));
    await throwIfNotOk(response, i18n.t("errors:file_view_load_failed"));
    return response.blob();
  }

  static async purgeTrashRecord(projectName: string, recordId: string): Promise<{ success: boolean }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/trash/${encodeURIComponent(recordId)}`, {
      method: "DELETE",
    });
  }

  static async emptyTrash(projectName: string): Promise<{ purged: number }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/trash`, { method: "DELETE" });
  }

  /**
   * User/project memory APIs have identical shapes with different prefixes, so both scopes share these methods.
   * Call sites in the same file-cabinet component change only scope, not invocation.
   */
  static async getAgentMemory(
    scope: AgentMemoryScope,
    options: { signal?: AbortSignal } = {}
  ): Promise<AgentMemoryOverview> {
    return this.request(agentMemoryBase(scope), { signal: options.signal });
  }

  /** Memory bodies are raw Markdown with frontmatter, sent/received as text/plain without parsing. */
  static async getAgentMemoryFile(
    scope: AgentMemoryScope,
    filename: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<string> {
    const url = `${agentMemoryBase(scope)}/files/${encodeURIComponent(filename)}`;
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { signal: options.signal }));
    await throwIfNotOk(response, i18n.t("errors:memory_file_load_failed"));
    return response.text();
  }

  /** Unconditional overwrite: creation and saving share one PUT, without server conflict detection. */
  static async saveAgentMemoryFile(
    scope: AgentMemoryScope,
    filename: string,
    content: string
  ): Promise<{ name: string }> {
    const url = `${agentMemoryBase(scope)}/files/${encodeURIComponent(filename)}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, {
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: content,
      })
    );
    await throwIfNotOk(response, i18n.t("errors:memory_file_save_failed"));
    return response.json() as Promise<{ name: string }>;
  }

  static async deleteAgentMemoryFile(
    scope: AgentMemoryScope,
    filename: string
  ): Promise<{ name: string }> {
    return this.request(`${agentMemoryBase(scope)}/files/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    });
  }

  static async clearAgentMemory(scope: AgentMemoryScope): Promise<{ cleared: boolean }> {
    return this.request(`${agentMemoryBase(scope)}/clear`, { method: "POST" });
  }

  static async deleteProject(name: string): Promise<SuccessResponse> {
    return this.request(`/projects/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  }

  static async listDeletedProjects(options: { signal?: AbortSignal } = {}): Promise<DeletedProjectListing> {
    return this.request("/deleted-projects", { signal: options.signal });
  }

  /** Restore a deleted project in full; if its old name is occupied, the server adds a suffix and returns the restored name. */
  static async restoreDeletedProject(id: string): Promise<{ name: string }> {
    return this.request(`/deleted-projects/${encodeURIComponent(id)}/restore`, { method: "POST" });
  }

  static async purgeDeletedProject(id: string): Promise<{ success: boolean }> {
    return this.request(`/deleted-projects/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  /**
   * Project-context video-model capabilities. `videoBackend` is an unsaved candidate model; omission resolves saved configuration.
   * `resolution` / `usesReferenceImages` supply duration-constraint evaluation context: undefined uses saved project tiers and generation mode.
   * Explicit `resolution: null` means the form selected Auto, sending an empty string without falling back to saved tiers.
   * Only the server applies narrowing rules; returned `duration_constraints` are already evaluated.
   */
  static async getVideoCapabilities(
    name: string,
    options: VideoCapabilitiesQuery = {}
  ): Promise<VideoCapabilities> {
    return this.request(
      `/projects/${encodeURIComponent(name)}/video-capabilities${videoCapabilitiesQuery(options)}`,
      { signal: options.signal },
    );
  }

  /** Video-model capabilities without project context, as in creation wizards, resolved from the candidate model; parameters match getVideoCapabilities. */
  static async getModelVideoCapabilities(
    videoBackend: string,
    options: Omit<VideoCapabilitiesQuery, "videoBackend"> = {}
  ): Promise<VideoCapabilities> {
    return this.request(`/providers/video-capabilities${videoCapabilitiesQuery({ ...options, videoBackend })}`, {
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

  // ==================== Editing timelines and output ====================

  /** Mechanically create a timeline from the current script using whole clips and hard cuts; duplicate episode display names return 409, and no usable video returns 422. */
  static async createEditTimeline(projectName: string, episode: number, name: string): Promise<EditTimelineReadout> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/edit-timelines`, {
      method: "POST",
      body: JSON.stringify({ from: "script", name }),
    });
  }

  private static editTimelinePath(projectName: string, timelineId: string): string {
    return `/projects/${encodeURIComponent(projectName)}/edit-timelines/${encodeURIComponent(timelineId)}`;
  }

  /** Current final-render state; omitted narration version uses the project default, and omitted subtitles means burn them in. */
  static async getFinalCutStatus(
    projectName: string,
    timelineId: string,
    options: { narration?: TimelineNarration; subtitles?: SubtitleMode; signal?: AbortSignal } = {}
  ): Promise<FinalCutStatus> {
    const query = new URLSearchParams();
    if (options.narration) query.set("narration", options.narration);
    if (options.subtitles) query.set("subtitles", options.subtitles);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return this.request(`${this.editTimelinePath(projectName, timelineId)}/final-cut${suffix}`, {
      signal: options.signal,
    });
  }

  /**
   * Enqueue final rendering at the latest revision. Components call through actions/render.
   * @enqueue
   */
  static async renderFinalCut(
    projectName: string,
    timelineId: string,
    options: Partial<RenderOptions> = {}
  ): Promise<RenderSubmission> {
    return this.request(`${this.editTimelinePath(projectName, timelineId)}/final-cut`, {
      method: "POST",
      body: JSON.stringify(options),
    });
  }

  /** Current Jianying-draft state; omitted narration version uses the project default. */
  static async getJianyingDraftStatus(
    projectName: string,
    timelineId: string,
    options: { narration?: TimelineNarration; signal?: AbortSignal } = {}
  ): Promise<JianyingDraftStatus> {
    const suffix = options.narration ? `?${new URLSearchParams({ narration: options.narration }).toString()}` : "";
    return this.request(`${this.editTimelinePath(projectName, timelineId)}/jianying-draft${suffix}`, {
      signal: options.signal,
    });
  }

  /**
   * Enqueue Jianying-draft export at the latest revision; omitted narration version uses the project default. Components call through actions/render.
   * @enqueue
   */
  static async exportJianyingDraft(
    projectName: string,
    timelineId: string,
    options: { narration?: TimelineNarration } = {}
  ): Promise<RenderSubmission> {
    return this.request(`${this.editTimelinePath(projectName, timelineId)}/jianying-draft`, {
      method: "POST",
      body: JSON.stringify(options),
    });
  }

  /** Registered Jianying-draft download URL; substitute the local draft directory and Jianying version at download time. */
  static getJianyingDraftDownloadUrl(
    projectName: string,
    timelineId: string,
    draftPath: string,
    downloadToken: string,
    jianyingVersion: JianyingVersion,
    narration?: TimelineNarration
  ): string {
    const query = new URLSearchParams({
      draft_path: draftPath,
      download_token: downloadToken,
      jianying_version: jianyingVersion,
      ...(narration ? { narration } : {}),
    });
    return `${API_BASE}${this.editTimelinePath(projectName, timelineId)}/jianying-draft/download?${query.toString()}`;
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

  /** Episode editing timelines in creation order. */
  static async listEditTimelines(
    projectName: string,
    episode: number,
    options: { signal?: AbortSignal } = {},
  ): Promise<{ timelines: EditTimelineSummary[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/edit-timelines?episode=${episode}`,
      { signal: options.signal },
    );
  }

  /** Read result for the latest editing-timeline revision. */
  static async getEditTimeline(
    projectName: string,
    timelineId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<EditTimelineReadout> {
    return this.request(
      this.editTimelinePath(projectName, timelineId),
      { signal: options.signal },
    );
  }

  /** Rename a timeline: change only its display name without creating a revision. */
  static async renameEditTimeline(
    projectName: string,
    timelineId: string,
    name: string,
  ): Promise<EditTimelineSummary> {
    return this.request(
      this.editTimelinePath(projectName, timelineId),
      { method: "PATCH", body: JSON.stringify({ name }) },
    );
  }

  /** Delete a timeline with its final renders and Jianying drafts; the server rejects while render tasks remain queued or running. */
  static async deleteEditTimeline(projectName: string, timelineId: string): Promise<void> {
    await this.request(
      this.editTimelinePath(projectName, timelineId),
      { method: "DELETE" },
    );
  }

  /** Preview media layer for the latest timeline revision: narration audio and subtitle entries per video unit. */
  static async getEditTimelinePreviewMedia(
    projectName: string,
    timelineId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<EditTimelinePreviewMedia> {
    return this.request(
      `${this.editTimelinePath(projectName, timelineId)}/preview-media`,
      { signal: options.signal },
    );
  }

  /** Upload BGM: the server measures loudness and registers project-level media usable by every episode timeline. */
  static async uploadBgm(projectName: string, file: File): Promise<{ success: boolean; bgm: ProjectBgm }> {
    return API.postFileUpload(`/projects/${encodeURIComponent(projectName)}/upload/bgm`, file);
  }

  /**
   * Import a project ZIP. If upgrade adjusts content or cannot bring some content over and `confirmReport` is absent, reject with 409,
   * leaving no project data. The thrown error carries `confirmation_required` and `migration_report`; after user confirmation,
   * resubmit with `confirmReport`.
   */
  static async importProject(
    file: File,
    conflictPolicy: ImportConflictPolicy = "prompt",
    options: { confirmReport?: boolean } = {},
  ): Promise<ImportProjectResponse> {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("conflict_policy", conflictPolicy);
    if (options.confirmReport) formData.append("confirm_report", "true");

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
      const detail = typeof payload.detail === "string" ? payload.detail : i18n.t("errors:import_failed");
      const error = new Error(detail) as Error & {
        status?: number;
        detail?: string;
        errors?: string[];
        warnings?: string[];
        conflict_project_name?: string;
        confirmation_required?: boolean;
        migration_report?: WorkflowMigrationReport;
        migration_report_episodes?: { episode: number; title: string }[];
        diagnostics?: ImportFailureDiagnostics;
      };
      error.status = response.status;
      error.detail = detail;
      error.errors = Array.isArray(payload.errors) ? payload.errors : [];
      error.warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      if (typeof payload.conflict_project_name === "string") {
        error.conflict_project_name = payload.conflict_project_name;
      }
      if (payload.confirmation_required === true && payload.migration_report) {
        error.confirmation_required = true;
        error.migration_report = payload.migration_report as WorkflowMigrationReport;
        error.migration_report_episodes = Array.isArray(payload.episodes)
          ? (payload.episodes as { episode: number; title: string }[])
          : [];
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

  // ==================== Character management ====================

  static async addCharacter(
    projectName: string,
    name: string,
    description: string,
    voiceStyle: string = ""
  ): Promise<AssetCreateResult> {
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

  // ==================== Character-derivative management ====================

  /**
   * Derivatives are character-local subidentities such as costume changes, transformations and disguises, named uniquely within the character and referenced as
   * `@[角色/衍生]`. These four methods only register them. See the three methods below for asset-image reads, generation and uploads; versioning
   * and image editing reuse generic endpoints with resource types `character_derivatives` / `character_derivative`.
   */
  static async addCharacterDerivative(
    projectName: string,
    charName: string,
    name: string,
    description: string
  ): Promise<SuccessResponse> {
    return this.request(
      derivativesPath(projectName, charName),
      {
        method: "POST",
        body: JSON.stringify({ name, description }),
      }
    );
  }

  static async updateCharacterDerivative(
    projectName: string,
    charName: string,
    derivativeName: string,
    description: string
  ): Promise<SuccessResponse> {
    return this.request(
      `${derivativesPath(projectName, charName)}/${encodeURIComponent(derivativeName)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ description }),
      }
    );
  }

  static async renameCharacterDerivative(
    projectName: string,
    charName: string,
    derivativeName: string,
    newName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `${derivativesPath(projectName, charName)}/${encodeURIComponent(derivativeName)}/rename`,
      {
        method: "POST",
        body: JSON.stringify({ new_name: newName }),
      }
    );
  }

  static async deleteCharacterDerivative(
    projectName: string,
    charName: string,
    derivativeName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `${derivativesPath(projectName, charName)}/${encodeURIComponent(derivativeName)}`,
      {
        method: "DELETE",
      }
    );
  }

  /**
   * Read each character derivative's asset image and staleness flag. Staleness compares artifact manifests with canonical state, requiring file reads
   * and fingerprints; project data omits it, so fetch it separately on demand.
   */
  static async getCharacterDerivativeSheets(
    projectName: string,
    charName: string,
    options?: { signal?: AbortSignal }
  ): Promise<{ success: boolean; derivatives: Record<string, CharacterDerivativeStatus> }> {
    return this.request(derivativesPath(projectName, charName), { signal: options?.signal });
  }

  /**
   * Submit derivative asset-image generation. Instructions combine its own appearance-change description and fixed guards; the request body has no prompt.
   * @enqueue
   */
  static async generateCharacterDerivative(
    projectName: string,
    charName: string,
    derivativeName: string
  ): Promise<{ success: boolean; task_id: string; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/character/${encodeURIComponent(charName)}/derivatives/${encodeURIComponent(derivativeName)}`,
      { method: "POST" }
    );
  }

  /**
   * Upload an image to replace a derivative asset image as its current version, adding version history. Uploaded images are finished work:
   * they require no base image and are not made stale by base-image regeneration or appearance-change edits.
   */
  static async uploadCharacterDerivativeSheet(
    projectName: string,
    charName: string,
    derivativeName: string,
    file: File
  ): Promise<ShotUploadResult> {
    return API.postFileUpload<ShotUploadResult>(
      `${derivativesPath(projectName, charName)}/${encodeURIComponent(derivativeName)}/upload`,
      file
    );
  }

  // ==================== Asset-image state and batch generation ====================

  /** Each project asset image's manifest-based state, including derivatives, and whether its description is missing. */
  static async getAssetSheetStatus(
    projectName: string,
    options?: { signal?: AbortSignal }
  ): Promise<{ assets: AssetSheetStatusRow[] }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/asset-sheets/status`, {
      signal: options?.signal,
    });
  }

  /** Plan a batch without creating tasks: generation targets, skipped items and estimated cost where calculable. */
  static async previewAssetSheetBatch(
    projectName: string,
    scope: AssetSheetBatchScope,
    options?: { signal?: AbortSignal }
  ): Promise<AssetSheetBatchPreview> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/asset-sheets/batch/preview`, {
      method: "POST",
      body: JSON.stringify(scope),
      signal: options?.signal,
    });
  }

  /**
   * Submit a batch and return member tasks immediately; callers track the whole batch's terminal state through tasks.
   * @enqueue
   */
  static async submitAssetSheetBatch(
    projectName: string,
    scope: AssetSheetBatchScope
  ): Promise<AssetSheetBatchSubmitted> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/asset-sheets/batch`, {
      method: "POST",
      body: JSON.stringify(scope),
    });
  }

  /** Plan an episode's storyboard-image/video batch without tasks: targets, skipped items and estimated cost where calculable. */
  static async previewStoryboardBatch(
    projectName: string,
    episode: number,
    kind: StoryboardBatchKind,
    options: { signal?: AbortSignal } = {}
  ): Promise<StoryboardBatchPreview> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/${kind}/batch/preview`,
      { method: "POST", signal: options.signal }
    );
  }

  /**
   * Submit an episode's storyboard-image/video batch; failed whole-batch video admission returns the decision without creating tasks.
   * @enqueue
   */
  static async submitStoryboardBatch(
    projectName: string,
    episode: number,
    kind: StoryboardBatchKind
  ): Promise<StoryboardBatchSubmitted> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/${kind}/batch`, {
      method: "POST",
    });
  }

  /** How many current artifacts regenerating this asset image would make stale. */
  static async getAssetRegenerationImpact(
    projectName: string,
    assetType: AssetSheetType,
    name: string,
    derivativeName?: string,
    options?: { signal?: AbortSignal },
  ): Promise<AssetRegenerationImpact> {
    const query = derivativeName ? `?${new URLSearchParams({ derivative_name: derivativeName })}` : "";
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/asset-sheets/${assetType}/${encodeURIComponent(name)}/regeneration-impact${query}`,
      { signal: options?.signal },
    );
  }

  // ==================== Project scene management ====================

  static async addProjectScene(
    projectName: string,
    name: string,
    description: string
  ): Promise<AssetCreateResult> {
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

  // ==================== Project prop management ====================

  static async addProjectProp(
    projectName: string,
    name: string,
    description: string
  ): Promise<AssetCreateResult> {
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

  // ==================== Project product management ====================

  static async addProjectProduct(
    projectName: string,
    name: string,
    description: string,
    brand?: string,
    sellingPoints?: string[]
  ): Promise<AssetCreateResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/products`,
      {
        method: "POST",
        body: JSON.stringify({
          name,
          description,
          ...(brand ? { brand } : {}),
          ...(sellingPoints?.length ? { selling_points: sellingPoints } : {}),
        }),
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

  // ==================== Project asset renaming ====================

  /**
   * Cascade-rename a project asset. `dryRun: true` returns only impact counts for episodes, references and files to update;
   * preview and execution share backend scan logic so confirmation counts match actual execution.
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

  /**
   * Merge a project asset into same-type `target`, limited to characters, scenes and props. `dryRun: true` returns per-episode
   * impact only; preview/execution share the same backend scan so confirmation counts match actual execution.
   */
  static async mergeProjectAsset(
    projectName: string,
    assetType: MergeableAssetType,
    name: string,
    target: string,
    options: { asDerivative?: boolean; dryRun?: boolean; signal?: AbortSignal } = {}
  ): Promise<AssetMergeResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/${ASSET_TYPE_PATH[assetType]}/${encodeURIComponent(name)}/merge`,
      {
        method: "POST",
        body: JSON.stringify({
          target,
          as_derivative: options.asDerivative ?? false,
          dry_run: options.dryRun ?? false,
        }),
        signal: options.signal,
      }
    );
  }

  /**
   * Read-only references before asset deletion: per-episode counts of script/draft references that would become dangling,
   * using rename's scan logic. Deletion neither rewrites these references nor rejects referenced assets.
   */
  static async previewProjectAssetDeletion(
    projectName: string,
    assetType: ProjectAssetType,
    name: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<AssetDeletionPreview> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/${ASSET_TYPE_PATH[assetType]}/${encodeURIComponent(name)}?dry_run=true`,
      { method: "DELETE", signal: options.signal }
    );
  }

  static async deleteProjectAsset(
    projectName: string,
    assetType: ProjectAssetType,
    name: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/${ASSET_TYPE_PATH[assetType]}/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    );
  }

  // ==================== Scene management ====================

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

  /** Preview the current asset-description draft without saving; locate derivatives by both base and derivative names. */
  static async previewAssetPrompt(
    projectName: string,
    assetType: ProjectAssetType,
    name: string,
    description: string,
    options?: { signal?: AbortSignal; derivativeName?: string },
  ): Promise<RenderedPromptPreview> {
    const derivativePath = options?.derivativeName === undefined
      ? ""
      : `/derivatives/${encodeURIComponent(options.derivativeName)}`;
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/${ASSET_TYPE_PATH[assetType]}/${encodeURIComponent(name)}${derivativePath}/prompt-preview`,
      { method: "POST", body: JSON.stringify({ description }), signal: options?.signal },
    );
  }

  /**
   * Final-prompt preview per entry: separate storyboard-image/video text, verbatim equal to text sent to the model at execution.
   *
   * Read-only: no provider requests or charges. Read **saved** script content; unsaved drafts still preview the previous
   * saved result. The backend localizes unavailability reasons by request language; the frontend does not translate again.
   */
  static async previewScriptItemPrompts(
    projectName: string,
    itemId: string,
    scriptFile: string,
    options?: { signal?: AbortSignal },
  ): Promise<ItemPromptPreview> {
    const query = new URLSearchParams({ script_file: scriptFile }).toString();
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/script-items/${encodeURIComponent(itemId)}/prompt-preview?${query}`,
      { signal: options?.signal },
    );
  }

  /** Update episode top-level metadata, currently title only. The script's top-level title is authoritative; the backend mirrors it to project.json. */
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

  /**
   * Enter/rewrite episode source text. Sourceless episodes gain their own source after saving; whole-source extracted episodes return 409.
   * `sourceKind` applies only to drama, preserving existing kind when omitted. If a kind change would stale existing episode script plans,
   * omitting `confirm` writes nothing and returns `needs_confirmation` with affected episodes.
   */
  static async updateEpisodeSource(
    projectName: string,
    episode: number,
    text: string,
    sourceKind?: SourceKind,
    confirm = false
  ): Promise<EpisodeSourceWriteResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/source`,
      {
        method: "PUT",
        body: JSON.stringify(sourceKind ? { text, source_kind: sourceKind, confirm } : { text, confirm }),
      }
    );
  }

  // ==================== script_plan → prompt_authoring content confirmation ====================

  /** Read structured episode script_plan intermediate content and confirmation state for Web rendering/editing. */
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

  /** Save manually/Agent-edited structured intermediate content and return the latest state, pending confirmation again.
   *
   * Supply the GET content fingerprint in `baseFingerprint`: competing script_plan writes during editing, such as Agent promotion,
   * return server 409 without persistence, preventing silent overwrites. Omission skips the comparison. */
  static async saveScriptReviewContent(
    projectName: string,
    episode: number,
    content: DramaNormalizedScript | NarrationScriptPlanDraft | ReferenceScriptPlanDraft,
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

  /**
   * Prompt authoring (AI write / rewrite), using the same service command as Agent generate_episode_script; submission immediately returns a generation batch.
   * Explicit rewrites overwriting existing content without valid `overwrite_revision` return 409 with the loss list in `diagnostic.prompt_overwrite`.
   * Save additional instructions per episode with the request.
   * @enqueue
   */
  static async authorPrompts(
    projectName: string,
    episode: number,
    body: AuthorPromptsRequest
  ): Promise<AuthorPromptsResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/prompt-authoring`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }

  /** Save only this episode's prompt-authoring additional instructions for Agent handoff, without submitting generation. */
  static async savePromptAuthoringInstructions(
    projectName: string,
    episode: number,
    instructions: string
  ): Promise<{ success: boolean }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/prompt-authoring/instructions`,
      { method: "PUT", body: JSON.stringify({ instructions }) }
    );
  }

  /**
   * AI script planning, using Agent generate_script_plan's service command; submission immediately returns a generation batch.
   * New planning fully replaces this episode's current plan and drafts; callers obtain confirmation first. Save additional instructions per episode with the request.
   * @enqueue
   */
  static async planScript(
    projectName: string,
    episode: number,
    body: PlanScriptRequest
  ): Promise<PlanScriptResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/script-plan`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }

  /**
   * AI ad/short-film script generation uses Agent generate_episode_script's service command; immediately return a generation batch and write results directly as a formal script.
   * Full regeneration (`regenerate`) replacing existing formal scripts without valid `overwrite_revision` returns 409, with the loss list in `diagnostic.script_overwrite`.
   * @enqueue
   */
  static async generateAdScript(
    projectName: string,
    episode: number,
    body: GenerateAdScriptRequest
  ): Promise<PlanScriptResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/ad-script`,
      { method: "POST", body: JSON.stringify(body) }
    );
  }

  /** Save only this episode's additional AI planning instructions for Agent handoff, without submitting generation. */
  static async saveScriptPlanInstructions(
    projectName: string,
    episode: number,
    instructions: string
  ): Promise<{ success: boolean }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/script-plan/instructions`,
      { method: "PUT", body: JSON.stringify({ instructions }) }
    );
  }

  /**
   * Explicit user confirmation converts the whole script_plan to a formal script and enables prompt_authoring visuals.
   * Existing formal scripts require `overwriteRevision`, the overwrite list's `revision`; absence or mismatch with current formal content returns 409.
   * `diagnostic.script_overwrite` includes the shots that would be removed by the current overwrite and server-written loss-list `text`.
   */
  static async confirmScriptReview(
    projectName: string,
    episode: number,
    options: { overwriteRevision?: string } = {}
  ): Promise<ScriptReviewState> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/script-review/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ overwrite_revision: options.overwriteRevision ?? null }),
      }
    );
  }

  // ==================== Drafts: repairable / Agent-editable ====================

  /** Summaries of drafts currently present for this episode. */
  static async listEpisodeDrafts(
    projectName: string,
    episode: number,
    options: { signal?: AbortSignal } = {}
  ): Promise<{ episode: number; drafts: EpisodeDraftSummary[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/drafts`,
      { signal: options.signal }
    );
  }

  /** Draft presentation: violations and downgrade hints positioned per entry; Agent-editable drafts omit their body. */
  static async getEpisodeDraft(
    projectName: string,
    episode: number,
    docType: DraftDocType,
    options: { signal?: AbortSignal } = {}
  ): Promise<EpisodeDraftView> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/drafts/${docType}`,
      { signal: options.signal }
    );
  }

  /** Save manual repairs: the server revalidates everything, adopting formal content when violations clear or returning a refreshed draft view otherwise. */
  static async saveEpisodeDraft(
    projectName: string,
    episode: number,
    docType: DraftDocType,
    content: Record<string, unknown>,
    baseRevision: string
  ): Promise<SaveEpisodeDraftResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/drafts/${docType}`,
      {
        method: "PUT",
        body: JSON.stringify({ content, base_revision: baseRevision }),
      }
    );
  }

  /**
   * AI repair shares the Agent service command, enqueued as a text task and returning a generation batch immediately. Modify only violating entries,
   * revalidate afterward, and adopt when violations clear or write back the draft otherwise. Do not save additional instructions.
   * @enqueue
   */
  static async repairEpisodeDraft(
    projectName: string,
    episode: number,
    docType: DraftDocType,
    baseRevision: string,
    instructions: string | null
  ): Promise<DraftRepairResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/drafts/${docType}/repair`,
      {
        method: "POST",
        body: JSON.stringify({ base_revision: baseRevision, instructions }),
      }
    );
  }

  /** Discard the draft and return to formal content. */
  static async discardEpisodeDraft(
    projectName: string,
    episode: number,
    docType: DraftDocType,
    baseRevision: string | null
  ): Promise<{ episode: number; doc_type: DraftDocType; discarded: boolean }> {
    const query = `?base_revision=${encodeURIComponent(baseRevision ?? "")}`;
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/episodes/${episode}/drafts/${docType}${query}`,
      { method: "DELETE" }
    );
  }

  // ==================== Storyboard management: narration/commentary ====================

  /**
   * Narration/commentary storyboard PATCH; drama uses {@link API.updateScene}. `updates` must include
   * `script_file`; other optional allowlisted fields are `duration_seconds`, `segment_break`, `novel_text`,
   * `image_prompt`, `video_prompt`, `note`,
   * `characters_in_segment`, `scenes`, `props`. The backend field list is authoritative;
   * mirrors server/routers/projects.py UpdateSegmentRequest.
   * Keep Record for spread-call compatibility.
   */
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

  // ==================== Storyboard management: ads/short films ====================

  /** Update one ad-script storyboard using allowlisted fields such as spoken copy, section, duration and references. */
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

  /**
   * Move storyboard `itemId` after `afterId`, or first with `null`, across all forms, moving artifacts with the storyboard.
   * The server uses the current script revision.
   */
  static async moveScriptItem(
    projectName: string,
    scriptFile: string,
    itemId: string,
    afterId: string | null
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/script-items/${encodeURIComponent(itemId)}/move`,
      {
        method: "POST",
        body: JSON.stringify({ script_file: scriptFile, after_id: afterId }),
      }
    );
  }

  /**
   * Add a pending-authoring storyboard for drama/narration/ads: insert after `afterId` when supplied, otherwise append
   * at the end, or first in an empty script. Use the current script revision; concurrent writes return 409. The new shot never inherits
   * artifacts or version history from an old same-ID shot. Narration bodies are voiceover content, requiring `novelText`.
   */
  static async insertScriptItem(
    projectName: string,
    scriptFile: string,
    options: { afterId?: string; novelText?: string } = {}
  ): Promise<SuccessResponse & { item: NarrationSegment | DramaScene | AdShot | null }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/script-items`, {
      method: "POST",
      body: JSON.stringify({
        script_file: scriptFile,
        ...(options.afterId !== undefined ? { after_id: options.afterId } : {}),
        ...(options.novelText !== undefined ? { novel_text: options.novelText } : {}),
      }),
    });
  }

  /** Start blank: create an empty formal script when none exists for this episode, discarding unconfirmed planning. */
  static async startBlankScript(projectName: string, episode: number): Promise<SuccessResponse & { script_file: string }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/blank-script`, {
      method: "POST",
    });
  }

  /** Remove storyboard `itemId` and its artifacts, using the current script revision server-side. */
  static async removeScriptItem(
    projectName: string,
    itemId: string,
    scriptFile: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/script-items/${encodeURIComponent(itemId)}?script_file=${encodeURIComponent(scriptFile)}`,
      { method: "DELETE" }
    );
  }

  // ==================== File management ====================

  static async uploadFile(
    projectName: string,
    uploadType: string,
    file: File,
    name: string | null = null,
    options: {
      onConflict?: "fail" | "replace" | "rename";
      /** Source only: whole_source registers a whole-source file; episode adds an episode with its own source at the end of broadcast order. */
      role?: "whole_source" | "episode";
      /** Source whole_source only: file index after registration in the whole-source list; omission appends at the end. */
      insertAt?: number;
      /** Source only: source-file kind for drama projects, defaulting to novel. */
      sourceKind?: SourceKind;
      /**
       * Source whole_source only: revision of the confirmed affected-episode list. If inserting/overwriting a whole-source file affects extracted episodes,
       * uploads without it write nothing and return `status=confirmation_required`, affected episodes and `revision`.
       */
      revision?: string;
      signal?: AbortSignal;
    } = {}
  ): Promise<{
    success: boolean;
    path?: string;
    /** Whole-source file insertion/overwrite: whether applied and the affected-episode list. */
    status?: SourceFileChangeResponse["status"];
    impact?: SourceFileChangeResponse["impact"];
    revision?: string;
    url?: string;
    filename?: string;
    /** role=episode: newly registered episode ID. */
    episode?: number;
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
    if (uploadType === "source" && options.role) {
      qsParts.push(`role=${encodeURIComponent(options.role)}`);
    }
    if (uploadType === "source" && options.insertAt !== undefined) {
      qsParts.push(`insert_at=${options.insertAt}`);
    }
    if (uploadType === "source" && options.sourceKind) {
      qsParts.push(`source_kind=${options.sourceKind}`);
    }
    if (uploadType === "source" && options.revision) {
      qsParts.push(`revision=${encodeURIComponent(options.revision)}`);
    }
    const qs = qsParts.join("&");
    const url = `/projects/${encodeURIComponent(projectName)}/upload/${uploadType}${qs ? "?" + qs : ""}`;

    const response = await fetch(`${API_BASE}${url}`, withAuth(url, {
      method: "POST",
      body: formData,
      signal: options.signal,
    }));

    if (response.status === 409) {
      let detail: { existing?: string; suggested_name?: string; message?: string } | null = null;
      try {
        const body = (await response.json()) as { detail?: { existing?: string; suggested_name?: string; message?: string } };
        detail = body?.detail ?? null;
      } catch {
        /* ignore */
      }
      // Backend SourceLoader ConflictError always includes existing + suggested_name;
      // if detail lacks fields, treat it as a protocol error and throw a generic error identifying the filename instead of inventing fallbacks:
      // avoid guessing suggested_name values inconsistent with backend naming rules and misleading users
      if (!detail?.existing || !detail?.suggested_name) {
        throw new Error(i18n.t("errors:upload_conflict_incomplete", { filename: file.name }));
      }
      throw new ConflictError(
        detail.existing,
        detail.suggested_name,
        detail.message ?? "conflict",
      );
    }

    await throwIfNotOk(response, i18n.t("errors:upload_failed"));
    return (await response.json()) as {
      success: boolean;
      path?: string;
      status?: SourceFileChangeResponse["status"];
      impact?: SourceFileChangeResponse["impact"];
      revision?: string;
      url: string;
      filename?: string;
      normalized?: boolean;
      original_kept?: boolean;
      original_filename?: string;
      used_encoding?: string | null;
      chapter_count?: number;
    };
  }

  /** Single-file multipart POST upload, returning the JSON response body. */
  private static async postFileUpload<T>(url: string, file: File): Promise<T> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { method: "POST", body: formData }));
    await throwIfNotOk(response, i18n.t("errors:upload_failed"));
    return (await response.json()) as T;
  }

  /** Upload a storyboard image/video to replace its AI-generated asset for storyboard-image-to-video, including grid storyboards. */
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

  // ==================== Storyboard end frames ====================
  //
  // Three endpoints share one destination: upload or select an existing project image, normalize to a PNG snapshot at
  // end_frames/scene_{id}.png, overwriting in place; clearing deletes the snapshot and empties the field. Returned
  // end_frame_image is project-relative; replacement keeps the path, using asset fingerprints for cache busting.

  /** Upload any image as this storyboard's end frame. */
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

  /** Select an existing project image as this storyboard's end frame, copying a snapshot without creating a reference. */
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

  /** Clear this storyboard's end frame. */
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

  /** Upload a video unit's final video. */
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

  /**
   * Get the authoritative workflow plan for this request without side effects: no enqueueing or project writes.
   * `confirmed_request_durations` affects only this resolution.
   */
  static async getWorkflowPlan(
    projectName: string,
    request: WorkflowPlanRequest = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<WorkflowPlan> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/workflow-plan`, {
      method: "POST",
      body: JSON.stringify(request),
      signal: options.signal,
    });
  }

  /** Project production state: without an episode, choose the first incomplete episode in ledger order or a project-level action as the next step. */
  static async getWorkflowStatus(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<WorkflowStatus> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/workflow-status`, {
      signal: options.signal,
    });
  }

  /** Recommended next step for each episode in ledger order. */
  static async getEpisodeNextSteps(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<{ episodes: EpisodeNextStep[] }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/workflow-status/episodes`, {
      signal: options.signal,
    });
  }

  /** Episode editing overview: timeline count, issue count for the most recently modified timeline, and timelines whose final renders are outdated. */
  static async getEpisodeEditOverview(
    projectName: string,
    episode: number,
    options: { signal?: AbortSignal } = {}
  ): Promise<EpisodeEditOverview> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/edit-overview`, {
      signal: options.signal,
    });
  }

  /**
   * Retry the project-data upgrade chain using Agent retry_project_migration's service command.
   * Continued failure returns 422 with raw failure in `diagnostic.reason` and structured detail in `diagnostic.details`.
   */
  static async retryProjectMigration(projectName: string): Promise<{ success: boolean }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/migration/retry`, {
      method: "POST",
    });
  }

  /**
   * Record that the user viewed the `migratedAt` upgrade report so production state stops returning it. Succeed even without a pending report.
   * Do not acknowledge if another report replaced it on disk; `acknowledged` is false.
   */
  static async acknowledgeMigrationReport(
    projectName: string,
    migratedAt: string,
  ): Promise<{ success: boolean; acknowledged: boolean }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/migration-report/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ migrated_at: migratedAt }),
    });
  }

  /**
   * Project-file URL.
   *
   * `cacheBust` is required: versioned URLs, usually using mtime_ns from assetFingerprints, receive immutable long-term caching;
   * changed files download again only with a new version. Explicit null for genuinely unversioned resources uses server negotiated caching.
   * `options.width` requests bitmap thumbnails; if the original is no wider than the target, the server returns the original.
   */
  static getFileUrl(
    projectName: string,
    path: string,
    cacheBust: number | string | null,
    options: FileUrlOptions = {}
  ): string {
    // Onboarding-demo placeholders are computed inline SVGs with data: URIs; use directly without wrapping in a project path.
    // Allow only data: here; no other image source currently carries its own scheme, so additional schemes would only add unused entry points.
    if (path.startsWith("data:")) {
      return path;
    }
    const base = `${API_BASE}/files/${encodeURIComponent(projectName)}/${path}`;
    const params: string[] = [];
    if (cacheBust != null && cacheBust !== "") {
      params.push(`v=${encodeURIComponent(String(cacheBust))}`);
    }
    if (options.width != null) {
      params.push(`w=${options.width}`);
    }
    return params.length ? `${base}?${params.join("&")}` : base;
  }

  // ==================== Source-file management ====================

  /**
   * Get source-file content
   */
  static async getSourceContent(
    projectName: string,
    filename: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<string> {
    const url = `/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, { signal: options.signal })
    );
    await throwIfNotOk(response, i18n.t("errors:file_content_load_failed"));
    return response.text();
  }

  /** Episodes view: whole-source episode segments, per-episode volume and first/last sentences, and unregistered source/ files. */
  static async getEpisodesView(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<EpisodesView> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes-view`, {
      signal: options.signal,
    });
  }

  /**
   * Manual segmentation writes directly to the ledger. If artifacts are affected, merges incorporate unsplit source, or `dryRun` is set, return a confirmation list without writing.
   * After confirmation, resubmit its episode IDs in `confirmEpisodes` and merged volume in `confirmMergedUnits`.
   */
  static async manualSplit(
    projectName: string,
    action: ManualSplitAction,
    options: { confirmEpisodes?: number[]; confirmMergedUnits?: number; dryRun?: boolean } = {}
  ): Promise<ManualSplitResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes-view/manual-split`, {
      method: "POST",
      body: JSON.stringify({
        ...action,
        confirm_episodes: options.confirmEpisodes ?? [],
        ...(options.confirmMergedUnits ? { confirm_merged_units: options.confirmMergedUnits } : {}),
        dry_run: options.dryRun ?? false,
      }),
    });
  }

  /**
   * AI episode planning runs window by window from the planning start to whole-source end, each window a queued text task; immediately return the first window's batch.
   * Existing planning returns 409; failed admission, such as missing whole source, returns 422. Do not save additional instructions in the project.
   * @enqueue
   */
  static async planEpisodes(
    projectName: string,
    instructions: string | null,
    gap: PlanningGap | null = null
  ): Promise<EpisodePlanningResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-planning`, {
      method: "POST",
      body: JSON.stringify(gap === null ? { instructions } : { instructions, gap }),
    });
  }

  /** Replanning scope from this episode: extracted episodes to replace and those already in production. Do not initiate or write anything. */
  static async previewEpisodeReplan(projectName: string, episode: number): Promise<ReplanPreview> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan`, {
      method: "POST",
      body: JSON.stringify({ episode, dry_run: true }),
    });
  }

  /**
   * Replan from this episode into a new candidate window by window, leaving the ledger unchanged until adoption; immediately return the first window's batch.
   * An existing candidate or running planning returns 409. Save additional instructions with the candidate.
   * @enqueue
   */
  static async startEpisodeReplan(
    projectName: string,
    episode: number,
    instructions: string | null
  ): Promise<EpisodePlanningResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan`, {
      method: "POST",
      body: JSON.stringify({ episode, instructions }),
    });
  }

  /**
   * Continue an interrupted replan from its ending through whole-source end, window by window, reusing the original additional instructions; immediately return
   * the first window's generation batch. Already complete/stale candidates or running planning return 409.
   * @enqueue
   */
  static async continueEpisodeReplan(projectName: string, candidateId: string): Promise<EpisodePlanningResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan/continue`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  }

  /**
   * Adopt a new plan. Without `revision`, return only server-written consequences; supply `revision` to adopt,
   * requesting confirmation again if consequences change between calls. `deleteRetired` deletes episodes retained as sourceless together with their artifacts.
   */
  static async adoptEpisodeReplan(
    projectName: string,
    candidateId: string,
    options: { revision?: string | null; deleteRetired?: boolean } = {}
  ): Promise<ReplanAdoptionResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan/adopt`, {
      method: "POST",
      body: JSON.stringify({
        candidate_id: candidateId,
        revision: options.revision ?? null,
        delete_retired: options.deleteRetired ?? false,
      }),
    });
  }

  /** Discard the new episode plan without changing the ledger. */
  static async discardEpisodeReplan(projectName: string, candidateId: string): Promise<void> {
    await this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan/discard`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  }

  /** Create an episode after `after`, or at the end of broadcast order when omitted. Supplied source gives it its own source. Return the new episode ID. */
  static async createEpisode(projectName: string, body: CreateEpisodeBody): Promise<{ episode: number }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /** Reorder broadcast: move this episode after `after`, or first when `after` is null. Violating source order between extracted episodes returns 409. */
  static async moveEpisode(projectName: string, episode: number, after: number | null): Promise<void> {
    await this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/move`, {
      method: "POST",
      body: JSON.stringify({ after }),
    });
  }

  /**
   * Delete an episode. Without `revision`, return only the server-written loss list; supply its `revision` to delete,
   * requesting confirmation again if the list changes between calls.
   */
  static async deleteEpisode(
    projectName: string,
    episode: number,
    revision: string | null = null
  ): Promise<EpisodeDeletionResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/delete`, {
      method: "POST",
      body: JSON.stringify({ revision }),
    });
  }

  /** Stop planning by cancelling queued windows; the running window finishes normally and extracted episodes remain. */
  static async stopEpisodePlanning(projectName: string): Promise<StopEpisodePlanningResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-planning/stop`, { method: "POST" });
  }

  /** Handle unregistered source/ files: add to whole source or use as episode source, deleting the original file afterward. */
  static async adoptSourceFile(
    projectName: string,
    filename: string,
    target: AdoptSourceFileTarget
  ): Promise<{ success: boolean; target: string; episode?: number }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/adopt`,
      { method: "POST", body: JSON.stringify(target) }
    );
  }

  /**
   * Change whole-source file kind for drama only. If plans for episodes already in production would become stale,
   * omitting `confirm` writes nothing and returns `needs_confirmation` with those episodes.
   */
  static async setSourceFileKind(
    projectName: string,
    filename: string,
    sourceKind: SourceKind,
    confirm = false
  ): Promise<SourceKindChangeResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/source-kind`,
      { method: "PUT", body: JSON.stringify({ source_kind: sourceKind, confirm }) }
    );
  }

  /**
   * Edit, replace, reorder or delete whole-source files. Without `revision`, changes affecting extracted episodes write nothing and return a server-written
   * affected-episode list and `revision`. Resubmit with it to execute; changed lists require confirmation again. Execute directly if no episodes are affected.
   */
  static async editSourceFile(
    projectName: string,
    filename: string,
    text: string,
    revision: string | null = null
  ): Promise<SourceFileChangeResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/text`,
      { method: "PUT", body: JSON.stringify({ text, revision }) }
    );
  }

  /** Replace a whole-source file with an upload, preserving position/name and retaining kind when `sourceKind` is omitted. Use `editSourceFile` confirmation rules. */
  static async replaceSourceFile(
    projectName: string,
    filename: string,
    file: File,
    options: { sourceKind?: SourceKind; revision?: string | null } = {}
  ): Promise<SourceFileChangeResponse> {
    const formData = new FormData();
    formData.append("file", file);
    if (options.sourceKind) formData.append("source_kind", options.sourceKind);
    if (options.revision) formData.append("revision", options.revision);
    const url = `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/replace`;
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { method: "POST", body: formData }));
    await throwIfNotOk(response, i18n.t("errors:file_replace_failed"));
    return (await response.json()) as SourceFileChangeResponse;
  }

  /** Move a whole-source file one position up/down, moving its extracted episodes together. Use `editSourceFile` confirmation rules. */
  static async moveSourceFile(
    projectName: string,
    filename: string,
    direction: "up" | "down",
    revision: string | null = null
  ): Promise<SourceFileChangeResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/move`,
      { method: "POST", body: JSON.stringify({ direction, revision }) }
    );
  }

  /** Update the ledger after changes outside ArcReel, remapping affected extracted episodes against the old snapshot. Use `editSourceFile` confirmation rules. */
  static async acceptExternalSourceChange(
    projectName: string,
    filename: string,
    revision: string | null = null
  ): Promise<SourceFileChangeResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/accept-external`,
      { method: "POST", body: JSON.stringify({ revision }) }
    );
  }

  /** Delete a whole-source file, equivalent to deleting all its text then removing it from whole source. Use `editSourceFile` confirmation rules. */
  static async deleteWholeSourceFile(
    projectName: string,
    filename: string,
    revision: string | null = null
  ): Promise<SourceFileChangeResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/source-files/${encodeURIComponent(filename)}/delete`,
      { method: "POST", body: JSON.stringify({ revision }) }
    );
  }

  /**
   * Delete a source file
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
    await throwIfNotOk(response, i18n.t("errors:file_delete_failed"));
    return response.json() as Promise<SuccessResponse>;
  }

  /**
   * Delete a character reference-audio sample, clearing its field and removing the file
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

  // ==================== Draft-file management ====================

  /**
   * Get draft content
   */
  static async getDraftContent(
    projectName: string,
    episode: number,
    stage: string
  ): Promise<string> {
    const url = `/projects/${encodeURIComponent(projectName)}/drafts/${episode}/${stage}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url)
    );
    await throwIfNotOk(response, i18n.t("errors:stage_file_load_failed"));
    return response.text();
  }

  /**
   * Save draft content
   */
  static async saveDraft(
    projectName: string,
    episode: number,
    stage: string,
    content: string
  ): Promise<SuccessResponse> {
    const url = `/projects/${encodeURIComponent(projectName)}/drafts/${episode}/${stage}`;
    const response = await fetch(
      `${API_BASE}${url}`,
      withAuth(url, {
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: content,
      })
    );
    await throwIfNotOk(response, i18n.t("errors:stage_file_save_failed"));
    return response.json() as Promise<SuccessResponse>;
  }

  /**
   * Delete a draft
   */
  static async deleteDraft(
    projectName: string,
    episode: number,
    stage: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/drafts/${episode}/${stage}`,
      { method: "DELETE" }
    );
  }

  // ==================== Project overview management ====================

  /**
   * Generate a project overview with AI
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
   * Update the project overview manually
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

  // ==================== Generation API ====================

  /**
   * Generate a storyboard image
   * @param projectName - Project name
   * @param segmentId - Storyboard ID
   * @param prompt - Image-generation prompt, either a string or structured object
   * @param scriptFile - Script filename
   * @enqueue
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
   * Generate video
   * @param projectName - Project name
   * @param segmentId - Storyboard ID
   * @param prompt - Video-generation prompt, either a string or structured object
   * @param scriptFile - Script filename
   * @param durationSeconds - Duration in seconds
   * @enqueue
   */
  static async generateVideo(
    projectName: string,
    segmentId: string,
    prompt: string | Record<string, unknown>,
    scriptFile: string,
    durationSeconds: number = 4,
  ): Promise<{ success: boolean; task_id: string; deduped: boolean; message: string }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/video/${encodeURIComponent(segmentId)}`,
      {
        method: "POST",
        body: JSON.stringify({
          prompt,
          script_file: scriptFile,
          duration_seconds: durationSeconds,
        }),
      }
    );
  }

  /**
   * Generate one narration voiceover, reading text from script novel_text server-side
   * @param projectName - Project name
   * @param segmentId - Storyboard ID
   * @param scriptFile - Script filename
   * @enqueue
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
   * Generate narration for the full episode, enqueueing only segments without narration that have source text
   * @param projectName - Project name
   * @param scriptFile - Script filename
   * @enqueue
   */
  static async generateEpisodeNarrationAudio(
    projectName: string,
    scriptFile: string
  ): Promise<{
    success: boolean;
    task_ids: string[];
    /** segment_id → task_id, containing only segments actually enqueued now and omitting skipped segments. */
    task_ids_by_segment: Record<string, string>;
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/tts`,
      {
        method: "POST",
        body: JSON.stringify({ script_file: scriptFile }),
      }
    );
  }

  /**
   * Read the voice enum from this project's effective audio backend for TTS audition selection.
   * configured=false means no audio provider is configured, so the frontend disables generation.
   * @param projectName - Project name
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
   * Submit a character TTS audition task for preview; call confirmCharacterVoiceSample afterward to persist the asset
   * @param projectName - Project name
   * @param charName - Character name
   * @param text - Text to synthesize
   * @param voice - Voice ID
   * @enqueue
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
   * Promote a generated, auditioned TTS sample to character reference_audio
   * @param projectName - Project name
   * @param charName - Character name
   * @param taskId - task_id returned by generateCharacterVoiceSample
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
   * Generate a character asset image
   * @param projectName - Project name
   * @param charName - Character name
   *
   * No prompt in the request body: use only the entry description stored in the project.
   * @enqueue
   */
  static async generateCharacter(
    projectName: string,
    charName: string
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
      }
    );
  }

  /**
   * Generate a scene asset image
   * @param projectName - Project name
   * @param sceneName - Scene name
   *
   * No prompt in the request body: use only the entry description stored in the project.
   * @enqueue
   */
  static async generateProjectScene(
    projectName: string,
    sceneName: string
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
      }
    );
  }

  /**
   * Generate a prop asset image
   * @param projectName - Project name
   * @param propName - Prop name
   *
   * No prompt in the request body: use only the entry description stored in the project.
   * @enqueue
   */
  static async generateProjectProp(
    projectName: string,
    propName: string
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
      }
    );
  }

  /**
   * Generate a product asset image (product sheet)
   * @param projectName - Project name
   * @param productName - Product name
   *
   * No prompt in the request body: use only the entry description stored in the project.
   * @enqueue
   */
  static async generateProjectProduct(
    projectName: string,
    productName: string
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
      }
    );
  }

  /**
   * Submit instruction-based image editing using the current image as the sole base and the instruction as the sole i2i prompt.
   * Replace current with the new image and move the old one into history. Storyboards (resourceType="storyboard") require scriptFile.
   * @enqueue
   */
  static async editImage(
    projectName: string,
    params: {
      resourceType: "character" | "scene" | "prop" | "product" | "storyboard" | "character_derivative";
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

  // ==================== Task queue API ====================

  static async getTask(taskId: string): Promise<TaskItem> {
    const { task } = await this.request<{ task: TaskItem }>(`/tasks/${encodeURIComponent(taskId)}`);
    return task;
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

  // ==================== Task cancellation API ====================

  static async cancelPreview(
    taskId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<{
    task: {
      task_id: string;
      task_type: string;
      resource_id: string;
      resource_ref?: EpisodeItemRef | null;
      status: string;
    };
    cascaded: { task_id: string; task_type: string; resource_id: string; resource_ref?: EpisodeItemRef | null }[];
  }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel-preview`, {
      signal: options.signal,
    });
  }

  static async cancelTask(
    taskId: string
  ): Promise<{
    cancelled: TaskItem[];
    skipped_terminal: TaskItem[];
  }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    });
  }

  static async retryTaskDownload(taskId: string): Promise<{ task: TaskItem }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/retry-download`, {
      method: "POST",
    });
  }

  static async cancelAllPreview(
    projectName: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<{ queued_count: number }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/tasks/cancel-all-preview`, {
      signal: options.signal,
    });
  }

  static async cancelAllQueued(
    projectName: string
  ): Promise<{ cancelled_count: number; skipped_running_count: number }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/tasks/cancel-all`,
      { method: "POST" }
    );
  }

  /** Subscribe to project events; reconnect automatically after disconnection, when the server sends a fresh snapshot. */
  static openProjectEventStream(options: ProjectEventStreamOptions): SseStreamHandle {
    const url = `${API_BASE}/projects/${encodeURIComponent(options.projectName)}/events/stream`;
    return openSseStream({
      url,
      headers: sseHeaders,
      onMessage(message) {
        const payload = parseSseJson(message.data, "project event");
        if (!payload) return;
        switch (message.event) {
          case "snapshot":
            options.onSnapshot?.(payload as unknown as ProjectEventSnapshotPayload);
            break;
          case "changes":
            options.onChanges?.(payload as unknown as ProjectChangeBatchPayload);
            break;
          case "project_deleted":
            options.onProjectDeleted?.(payload as unknown as ProjectDeletedPayload);
            break;
          case "assistant_session_resumed":
            options.onAssistantSessionResumed?.(payload as unknown as AssistantSessionResumedPayload);
            break;
          default:
            break;
        }
      },
      onError: sseErrorHandler(options.onError),
    });
  }

  // ==================== Version management API ====================

  /**
   * Get resource versions
   * @param projectName - Project name
   * @param resourceType - Resource type (storyboards, videos, characters, scenes, props)
   * @param resourceId - Resource ID
   */
  static async getVersions(
    projectName: string,
    resourceType: string,
    resourceId: string,
    options?: { signal?: AbortSignal },
  ): Promise<{
    resource_type: string;
    resource_id: string;
    current_version: number;
    versions: VersionInfo[];
  }> {
    return this.request(versionsResourcePath(projectName, resourceType, resourceId), { signal: options?.signal });
  }

  /**
   * Restore a specified version
   * @param projectName - Project name
   * @param resourceType - Resource type
   * @param resourceId - Resource ID
   * @param version - Version number to restore
   */
  static async restoreVersion(
    projectName: string,
    resourceType: string,
    resourceId: string,
    version: number
  ): Promise<SuccessResponse & { file_path?: string; asset_fingerprints?: Record<string, number> }> {
    return this.request(
      `${versionsResourcePath(projectName, resourceType, resourceId)}/restore/${version}`,
      {
        method: "POST",
      }
    );
  }

  // ==================== Style-reference image API ====================

  /**
   * Upload a style-reference image
   * @param projectName - Project name
   * @param file - Image file
   * @returns Result containing style_image, style_description and url
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

    await throwIfNotOk(response, i18n.t("errors:upload_failed"));

    return response.json() as Promise<{ success: boolean; style_image: string; style_description: string; url: string }>;
  }

  // ==================== Agent-session API ====================

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

  /** Cold-read session events; after is a seq cursor, with -1 meaning from the beginning. */
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
  ): Promise<AcceptedMessageResponse> {
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
   * Rewrite a historical user message: the server branches a new session and reruns it there.
   *
   * `sessionId` identifies the original session; response `session_id` identifies the new branch receiving the rewrite.
   * The endpoint automatically interrupts running sessions, so callers need not stop them first.
   *
   * `images` contains the anchor message's image attachments, entering the branch's first input with rewritten text,
   * using the same shape as the send endpoint.
   */
  static async rewriteAssistantMessage(
    projectName: string,
    sessionId: string,
    anchorEntryUuid: string,
    content: string,
    images?: ImagePayload[],
    clientKey?: string
  ): Promise<AcceptedMessageResponse & { origin_session_id: string | null }> {
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

  /**
   * Edit/delete a queued message: retract it from the Agent first, removing it from the queue only on successful retraction.
   * Successful edits return its content; if the Agent already accepted it, `outcome` is `accepted`.
   */
  static async withdrawQueuedMessage(
    projectName: string,
    sessionId: string,
    messageId: string,
    intent: QueuedMessageWithdrawal
  ): Promise<QueuedMessageWithdrawalResponse> {
    const path = `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/queued-messages/${encodeURIComponent(messageId)}`;
    return intent === "edit"
      ? this.request(`${path}/edit`, { method: "POST" })
      : this.request(path, { method: "DELETE" });
  }

  static async resendQueuedMessage(
    projectName: string,
    sessionId: string,
    messageId: string
  ): Promise<QueuedMessageResendResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/queued-messages/${encodeURIComponent(messageId)}/resend`,
      { method: "POST" }
    );
  }

  /**
   * Send a queued message immediately: retract first, then on success resubmit at now priority so the Agent interrupts its turn to handle it.
   * If already accepted, `outcome` is `accepted` and no resend occurs.
   */
  static async sendQueuedMessageNow(
    projectName: string,
    sessionId: string,
    messageId: string
  ): Promise<QueuedMessageSendNowResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/queued-messages/${encodeURIComponent(messageId)}/send-now`,
      { method: "POST" }
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

  /** Entry-stream SSE URL; after is a seq cursor, while streaming-client Last-Event-ID handles reconnection resume. */
  static getAssistantEntriesStreamUrl(
    projectName: string,
    sessionId: string,
    after: number = -1
  ): string {
    const base = `${API_BASE}${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/entries/stream`;
    return after >= 0 ? `${base}?after=${after}` : base;
  }

  /** Subscribe to session entries; event IDs are seq values, and reconnection automatically sends Last-Event-ID to resume. */
  static openAssistantEntriesStream(options: AssistantEntriesStreamOptions): SseStreamHandle {
    return openSseStream({
      url: this.getAssistantEntriesStreamUrl(options.projectName, options.sessionId, options.after ?? -1),
      headers: sseHeaders,
      onMessage(message) {
        const payload = parseSseJson(message.data, "session");
        if (payload) options.onEvent(message.event, payload);
      },
      onError: sseErrorHandler(options.onError),
    });
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

  // ==================== Usage-record read API ====================

  /**
   * Get a usage-record keyset page ordered by started_at descending.
   * The server excludes pending calls represented by tasks from results.
   */
  static async getUsageRecords(
    query: UsageRecordsQuery = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageRecordPage> {
    const params = new URLSearchParams();
    // Endpoint-trial project_name is an empty string; do not use truthiness.
    if (query.projectName !== undefined) params.append("project_name", query.projectName);
    if (query.providers?.length) params.append("provider", query.providers.join(","));
    if (query.models?.length) params.append("model", query.models.join(","));
    if (query.mediaTypes?.length) params.append("media_type", query.mediaTypes.join(","));
    if (query.statuses?.length) params.append("status", query.statuses.join(","));
    if (query.segmentIds?.length) params.append("segment_id", query.segmentIds.join(","));
    if (query.since) params.append("since", query.since);
    if (query.until) params.append("until", query.until);
    if (query.limit !== undefined) params.append("limit", String(query.limit));
    if (query.cursor) params.append("cursor", query.cursor);
    if (query.view) params.append("view", query.view);
    const search = params.toString();
    return this.request(`/usage/records${search ? "?" + search : ""}`, {
      signal: options.signal,
    });
  }

  /**
   * Get an Agent-session keyset page, aggregating by session_id with one row per session,
   * ordered by latest turn descending.
   */
  static async getUsageSessions(
    query: UsageSessionsQuery = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageAgentSessionPage> {
    const params = new URLSearchParams();
    if (query.projectName !== undefined) params.append("project_name", query.projectName);
    if (query.providers?.length) params.append("provider", query.providers.join(","));
    if (query.models?.length) params.append("model", query.models.join(","));
    if (query.since) params.append("since", query.since);
    if (query.until) params.append("until", query.until);
    if (query.limit !== undefined) params.append("limit", String(query.limit));
    if (query.cursor) params.append("cursor", query.cursor);
    const search = params.toString();
    return this.request(`/usage/sessions${search ? "?" + search : ""}`, {
      signal: options.signal,
    });
  }

  /** Get Agent-session details, one record per row in chronological order. */
  static async getUsageSession(
    sessionId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageAgentSessionDetail> {
    return this.request(`/usage/sessions/${encodeURIComponent(sessionId)}`, {
      signal: options.signal,
    });
  }

  /** Get one usage record's details, adding prompt, inputs and raw provider response beyond the list projection. */
  static async getUsageRecord(
    recordId: number,
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageRecordDetail> {
    return this.request(`/usage/records/${recordId}`, { signal: options.signal });
  }

  /** Fetch all overview aggregates at once: KPIs, daily trends, three-dimensional breakdowns, attention-needed and filter choices. */
  static async getUsageSummary(
    query: UsageSummaryQuery = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageSummary> {
    const params = new URLSearchParams();
    if (query.projectName !== undefined) params.append("project_name", query.projectName);
    if (query.provider) params.append("provider", query.provider);
    if (query.model) params.append("model", query.model);
    if (query.mediaType) params.append("media_type", query.mediaType);
    if (query.since) params.append("since", query.since);
    if (query.until) params.append("until", query.until);
    if (query.tz) params.append("tz", query.tz);
    if (query.view) params.append("view", query.view);
    const search = params.toString();
    return this.request(`/usage/summary${search ? "?" + search : ""}`, {
      signal: options.signal,
    });
  }

  // ==================== API Key management API ====================

  /** List all API Keys without full keys. */
  static async listApiKeys(options: { signal?: AbortSignal } = {}): Promise<ApiKeyInfo[]> {
    return this.request("/api-keys", { signal: options.signal });
  }

  /** Create an API Key, returning the full key only this once. */
  static async createApiKey(name: string, expiresDays?: number): Promise<CreateApiKeyResponse> {
    return this.request("/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, expires_days: expiresDays ?? null }),
    });
  }

  /** Delete/revoke the specified API Key. */
  static async deleteApiKey(keyId: number): Promise<void> {
    return this.request(`/api-keys/${keyId}`, { method: "DELETE" });
  }

  // ==================== Model catalog API ====================

  /** Search catalog entries by name/media type; an empty name lists all. */
  static async searchCatalogEntries(
    query: { q?: string; mediaType?: MediaType } = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<CatalogSearchResponse> {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.mediaType) params.set("media_type", query.mediaType);
    const qs = params.toString();
    return this.request(`/catalog/entries${qs ? `?${qs}` : ""}`, { signal: options.signal });
  }

  /** Hints before manual entry creation: whether the catalog already has a same-named model, and preset candidate durations inferred from video-model names. */
  static async getNewEntryHints(
    query: { name: string; mediaType: MediaType },
    options: { signal?: AbortSignal } = {}
  ): Promise<NewEntryHints> {
    const params = new URLSearchParams({ name: query.name, media_type: query.mediaType });
    return this.request(`/catalog/new-entry-hints?${params}`, { signal: options.signal });
  }

  /** Manually create and immediately persist an entry for an uncataloged model; manual entry in Sheets instead persists with Save and does not use this endpoint. */
  static async createCatalogEntry(entry: NewCatalogEntry): Promise<CatalogEntryHit> {
    return this.request("/catalog/entries", { method: "POST", body: JSON.stringify(entry) });
  }

  // ==================== Provider management API ====================

  /** Get all providers and their statuses. */
  static async getProviders(
    options: { signal?: AbortSignal } = {}
  ): Promise<{ providers: ProviderInfo[] }> {
    return this.request("/providers", { signal: options.signal });
  }

  /** Get provider configuration details, including field lists. */
  static async getProviderConfig(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderConfigDetail> {
    return this.request(`/providers/${encodeURIComponent(id)}/config`, { signal: options.signal });
  }

  /** Update provider configuration fields. */
  static async patchProviderConfig(
    id: string,
    patch: Record<string, string | null>
  ): Promise<void> {
    return this.request(`/providers/${encodeURIComponent(id)}/config`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  /** Read provider multipliers. Custom-provider IDs use `custom-<数据库 id>`. */
  static async getProviderMultiplier(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderMultiplierInfo> {
    return this.request(`/providers/${encodeURIComponent(id)}/multiplier`, { signal: options.signal });
  }

  /** Set provider multipliers; null restores defaults. Affect only calls settled afterward. */
  static async setProviderMultiplier(id: string, multiplier: number | null): Promise<ProviderMultiplierInfo> {
    return this.request(`/providers/${encodeURIComponent(id)}/multiplier`, {
      method: "PUT",
      body: JSON.stringify({ multiplier }),
    });
  }

  /** All provider models with enabled state, status markers and price summaries, shared by built-in/custom providers. */
  static async listProviderModels(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderModelsResponse> {
    return this.request(`/providers/${encodeURIComponent(id)}/models`, { signal: options.signal });
  }

  /** Fetch models through the provider's model-list API, returning all models and corresponding results; the UI excludes already-added models. */
  static async discoverProviderModels(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<FetchProviderModelsResponse> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/discover`, {
      method: "POST",
      signal: options.signal,
    });
  }

  /** Add a model by ID: built-in providers enable officially cataloged matches; unmatched IDs become user-added models. */
  static async addProviderModel(id: string, body: AddProviderModelRequest): Promise<AddProviderModelResponse> {
    return this.request(`/providers/${encodeURIComponent(id)}/models`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /** Enable/disable a provider model immediately; disabling also clears its default status. */
  static async setProviderModelEnabled(id: string, modelId: string, enabled: boolean): Promise<ProviderModelItem> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/enabled`, {
      method: "PUT",
      body: JSON.stringify({ model_id: modelId, enabled }),
    });
  }

  /** Read one provider model: list row, provider settings, matched entry and parameters supported by this provider. */
  static async getProviderModel(
    id: string,
    modelId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderModelDetail> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/${encodePath(modelId)}`, {
      signal: options.signal,
    });
  }

  /** Save provider settings from its model Sheet and return updated Sheet contents. */
  static async updateProviderModel(
    id: string,
    modelId: string,
    settings: ProviderModelSettingsUpdate
  ): Promise<ProviderModelDetail> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/${encodePath(modelId)}`, {
      method: "PATCH",
      body: JSON.stringify(settings),
    });
  }

  /** Restore official data by discarding user model-information edits, immediately affecting every provider using the model; irreversible. */
  static async restoreCatalogEntry(key: CatalogEntryKey): Promise<void> {
    await this.request("/catalog/entries/restore-official", { method: "POST", body: JSON.stringify(key) });
  }

  /** Remove a user-managed model from a provider. */
  static async removeProviderModel(id: string, modelId: string): Promise<void> {
    await this.request(`/providers/${encodeURIComponent(id)}/models/${encodePath(modelId)}`, { method: "DELETE" });
  }

  /** Global settings/project references to this provider model, for warnings before removal. */
  static async getProviderModelReferences(
    id: string,
    modelId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderModelReferences> {
    const params = new URLSearchParams({ model_id: modelId });
    return this.request(`/providers/${encodeURIComponent(id)}/model-references?${params}`, { signal: options.signal });
  }

  /** Test provider connectivity. */
  static async checkProviderConnectivity(id: string, credentialId?: number): Promise<ConnectivityCheckResult> {
    const params = credentialId != null ? `?credential_id=${credentialId}` : "";
    return this.request(`/providers/${encodeURIComponent(id)}/test${params}`, {
      method: "POST",
    });
  }

  // ==================== Object storage and provider asset libraries ====================

  static async getObjectStorageConnection(options: { signal?: AbortSignal } = {}): Promise<ObjectStorageSettings> {
    return this.request("/object-storage/connection", { signal: options.signal });
  }

  static async saveNewImageTarget(target: NewImageTarget): Promise<{ target: NewImageTarget }> {
    return this.request("/object-storage/new-image-target", { method: "PUT", body: JSON.stringify({ target }) });
  }

  static async saveObjectStorageConnection(data: SaveObjectStorageConnectionPayload): Promise<ObjectStorageConnection> {
    return this.request("/object-storage/connection", { method: "PUT", body: JSON.stringify(data) });
  }

  static async deleteObjectStorageConnection(): Promise<void> {
    return this.request("/object-storage/connection", { method: "DELETE" });
  }

  static async checkObjectStorageConnection(): Promise<{ success: boolean }> {
    return this.request("/object-storage/connection/test", { method: "POST" });
  }

  static async listAssetLibraryAccounts(options: { signal?: AbortSignal } = {}): Promise<AssetLibraryAccountList> {
    return this.request("/asset-library/accounts", { signal: options.signal });
  }

  static async createAssetLibraryAccount(data: CreateAssetLibraryAccountPayload): Promise<AssetLibraryAccount> {
    return this.request("/asset-library/accounts", { method: "POST", body: JSON.stringify(data) });
  }

  static async updateAssetLibraryAccount(
    accountId: number,
    data: UpdateAssetLibraryAccountPayload,
  ): Promise<AssetLibraryAccount> {
    return this.request(`/asset-library/accounts/${accountId}`, { method: "PATCH", body: JSON.stringify(data) });
  }

  static async deleteAssetLibraryAccount(accountId: number): Promise<void> {
    return this.request(`/asset-library/accounts/${accountId}`, { method: "DELETE" });
  }

  static async listAssetLibraryBindings(
    options: { signal?: AbortSignal } = {},
  ): Promise<{ credentials: AssetLibraryBindableCredential[] }> {
    return this.request("/asset-library/bindings", { signal: options.signal });
  }

  /** Confirm bindings against current inference-credential/account contents; initial binding, rebinding and reconfirmation share this endpoint. */
  static async confirmAssetLibraryBinding(
    credentialType: AssetLibraryCredentialType,
    credentialId: number,
    accountId: number,
  ): Promise<{ account_id: number; status: AssetLibraryBindingStatus }> {
    return this.request(`/asset-library/bindings/${credentialType}/${credentialId}`, {
      method: "PUT",
      body: JSON.stringify({ account_id: accountId }),
    });
  }

  static async deleteAssetLibraryBinding(credentialType: AssetLibraryCredentialType, credentialId: number): Promise<void> {
    return this.request(`/asset-library/bindings/${credentialType}/${credentialId}`, { method: "DELETE" });
  }

  static async listProviderAssets(
    accountId: number,
    params: { page: number; pageSize: number },
    options: { signal?: AbortSignal } = {},
  ): Promise<ProviderAssetPage> {
    const query = new URLSearchParams({ page: String(params.page), page_size: String(params.pageSize) });
    return this.request(`/asset-library/accounts/${accountId}/assets?${query.toString()}`, { signal: options.signal });
  }

  /** Associations and ingestion state for the selected image version across asset-library scopes. */
  static async getProviderAssetLinks(
    projectName: string,
    resourceType: string,
    resourceId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<ProviderAssetLinkPanel> {
    const query = new URLSearchParams({ resource_type: resourceType, resource_id: resourceId });
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links?${query.toString()}`, {
      signal: options.signal,
    });
  }

  /** Upload the currently selected image version to a library account; return its ingestion task immediately, then poll state. */
  static async uploadToProviderAssetLibrary(
    projectName: string,
    body: { resource_type: string; resource_id: string; account_id: number },
  ): Promise<ProviderAssetLink> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /** Select an available library image: retrieve the original, save it as a new version and link that version. */
  static async importProviderAsset(
    projectName: string,
    body: { resource_type: string; resource_id: string; account_id: number; asset_id: string },
  ): Promise<ProviderAssetImportResult> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links/import`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /** Unlink only the current relationship, without deleting the ArcReel image or provider asset. */
  static async unlinkProviderAsset(projectName: string, linkId: number): Promise<void> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links/${linkId}`, {
      method: "DELETE",
    });
  }

  static async refreshProviderAssetLink(projectName: string, linkId: number): Promise<ProviderAssetLink> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links/${linkId}/refresh`, {
      method: "POST",
    });
  }

  // ==================== Provider credential management API ====================

  static async listCredentials(providerId: string, options: { signal?: AbortSignal } = {}): Promise<{ credentials: ProviderCredential[] }> {
    return this.request(`/providers/${encodeURIComponent(providerId)}/credentials`, { signal: options.signal });
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
    await throwIfNotOk(response, i18n.t("errors:credential_upload_failed"));
    return response.json() as Promise<ProviderCredential>;
  }

  // ==================== Agent configuration / credential API ====================

  static async listAgentPresetProviders(options: { signal?: AbortSignal } = {}): Promise<PresetProvidersResponse> {
    return this.request("/agent/preset-providers", { signal: options.signal });
  }

  static async listAgentCredentials(options: { signal?: AbortSignal } = {}): Promise<{ credentials: AgentCredential[] }> {
    return this.request("/agent/credentials", { signal: options.signal });
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

  /** Select a catalog entry for one tier, using model vendor/official model name as `entryBinding`; return the updated credential. */
  static async bindAgentCredentialTier(
    id: number,
    tier: AgentCredentialTier,
    entryBinding: string,
  ): Promise<AgentCredential> {
    return this.request(`/agent/credentials/${id}/tiers/${tier}/binding`, {
      method: "PUT",
      body: JSON.stringify({ entry_binding: entryBinding }),
    });
  }

  /** Unbind one tier to resume automatic model-name matching; return the updated credential. */
  static async unbindAgentCredentialTier(id: number, tier: AgentCredentialTier): Promise<AgentCredential> {
    return this.request(`/agent/credentials/${id}/tiers/${tier}/binding`, { method: "DELETE" });
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

  // ==================== Custom-provider API ====================

  static async listCustomProviders(
    options: { signal?: AbortSignal } = {}
  ): Promise<{ providers: CustomProviderInfo[] }> {
    return this.request("/custom-providers", { signal: options.signal });
  }

  static async listEndpointCatalog(): Promise<{ endpoints: EndpointDescriptor[] }> {
    return this.request("/custom-providers/endpoints");
  }

  static async createCustomProvider(data: CustomProviderCreateRequest): Promise<CustomProviderInfo> {
    return this.request("/custom-providers", { method: "POST", body: JSON.stringify(data) });
  }

  static async getCustomProvider(id: number, options: { signal?: AbortSignal } = {}): Promise<CustomProviderInfo> {
    return this.request(`/custom-providers/${id}`, { signal: options.signal });
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

  static async discoverModels(
    data: { discovery_format: string; base_url: string; api_key: string },
    options: { signal?: AbortSignal } = {}
  ): Promise<DiscoverModelsResponse> {
    return this.request("/custom-providers/discover", {
      method: "POST",
      body: JSON.stringify(data),
      signal: options.signal,
    });
  }

  static async checkCustomConnectivity(data: { discovery_format: string; base_url: string; api_key: string }): Promise<{ success: boolean; message: string }> {
    return this.request("/custom-providers/test", { method: "POST", body: JSON.stringify(data) });
  }

  static async checkCustomConnectivityById(id: number): Promise<{ success: boolean; message: string }> {
    return this.request(`/custom-providers/${id}/test`, { method: "POST" });
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

  // ==================== Market API ====================

  static async listMarketSources(
    options: { signal?: AbortSignal } = {},
  ): Promise<MarketSourceListResponse> {
    return this.request("/market/sources", { signal: options.signal });
  }

  /** Fetch once when adding; reject unaccepted URLs, duplicates or fetch failures without persisting. */
  static async addMarketSource(body: {
    address: string;
    display_name?: string;
  }): Promise<MarketSourceInfo> {
    return this.request("/market/sources", { method: "POST", body: JSON.stringify(body) });
  }

  static async updateMarketSource(
    id: number,
    patch: { display_name?: string; is_enabled?: boolean },
  ): Promise<MarketSourceInfo> {
    return this.request(`/market/sources/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }

  /** Official market sources return 409. */
  static async deleteMarketSource(id: number): Promise<void> {
    return this.request(`/market/sources/${id}`, { method: "DELETE" });
  }

  /** ids must be a full permutation of all market-source IDs. */
  static async reorderMarketSources(ids: number[]): Promise<MarketSourceListResponse> {
    return this.request("/market/sources/order", { method: "PUT", body: JSON.stringify({ ids }) });
  }

  /** Refresh failures do not throw: store results in returned row status / last_error. */
  static async refreshMarketSource(id: number): Promise<MarketSourceInfo> {
    return this.request(`/market/sources/${id}/refresh`, { method: "POST" });
  }

  /**
   * Refresh all enabled sources, returning refreshed rows per source.
   * @param staleOnly - Automatic market-page refresh: only sources whose last successful refresh was over one hour ago.
   */
  static async refreshMarketSources(
    options: { staleOnly?: boolean; signal?: AbortSignal } = {},
  ): Promise<MarketSourceListResponse> {
    const query = options.staleOnly ? "?stale_only=true" : "";
    return this.request(`/market/refresh${query}`, { method: "POST", signal: options.signal });
  }

  /** Entries from enabled-source cached snapshots, ordered by source then name within source; read snapshots only without fetching. */
  static async listMarketEntries(
    options: { type?: string; signal?: AbortSignal } = {},
  ): Promise<MarketEntryListResponse> {
    const query = new URLSearchParams({ type: options.type ?? "endpoint" });
    return this.request(`/market/entries?${query}`, { signal: options.signal });
  }

  static async getMarketEntry(sourceId: number, slug: string, options: { signal?: AbortSignal } = {}): Promise<MarketEntryDetail> {
    return this.request(`/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}`, options);
  }

  static async getMarketEntryDefinition(sourceId: number, slug: string, options: { signal?: AbortSignal } = {}): Promise<{ definition: unknown; entry_matches_definition: boolean; definition_digest: string | null }> {
    return this.request(`/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}/definition`, options);
  }

  /** Use the definition loaded on the confirmation page for `definitionDigest`; changed content after server refetch returns 409 and refuses installation. */
  static async installMarketEntry(sourceId: number, slug: string, definitionDigest: string, overwriteEndpointId?: number): Promise<{ endpoint: CustomEndpointInfo; installation: MarketEntryInstallation }> {
    return this.request(`/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}/install`, {
      method: "POST", body: JSON.stringify({ definition_digest: definitionDigest, overwrite_endpoint_id: overwriteEndpointId }),
    });
  }

  /**
   * Fetch entry icons through the backend proxy. Session authentication cannot accompany `<img>`, so return Blob;
   * include the entry version in URLs to bypass browser cache on upgrades.
   */
  static async getMarketEntryIcon(
    sourceId: number,
    slug: string,
    version: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<Blob> {
    const url = `/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}/icon?v=${encodeURIComponent(version)}`;
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { signal: options.signal }));
    await throwIfNotOk(response, i18n.t("errors:market_icon_load_failed"));
    return response.blob();
  }

  // ==================== Official service API ====================
  // The frontend communicates only with the local server, which proxies all official-service calls and makes zero outbound calls while disabled.

  static async getOfficialService(options: { signal?: AbortSignal } = {}): Promise<OfficialServiceState> {
    return this.request("/official-service", { signal: options.signal });
  }

  static async updateOfficialService(patch: {
    enabled?: boolean;
    notice_seen?: boolean;
  }): Promise<OfficialServiceState> {
    return this.request("/official-service", { method: "PATCH", body: JSON.stringify(patch) });
  }

  /** Delete instance identity; the next official-service request generates a new one. */
  static async resetOfficialInstanceId(): Promise<OfficialServiceState> {
    return this.request("/official-service/instance-id/reset", { method: "POST" });
  }

  /** Installation counts/ratings for entries in enabled official market sources; throw if the service is disabled or unavailable. */
  static async listMarketEntryAggregates(
    options: { signal?: AbortSignal } = {},
  ): Promise<{ items: MarketEntryAggregate[] }> {
    return this.request("/market/entries/aggregates?type=endpoint", { signal: options.signal });
  }

  /** 1–5 stars; return 409 if the official service has no installation record for this instance. */
  static async rateMarketEntry(sourceId: number, slug: string, stars: number): Promise<void> {
    return this.request(`/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}/rating`, {
      method: "PUT",
      body: JSON.stringify({ stars }),
    });
  }

  /** Local validation matching official-service preflight, without outbound calls; report errors only. */
  static async checkMarketSubmission(
    draft: MarketSubmissionDraft,
    options: { signal?: AbortSignal } = {},
  ): Promise<{ diagnostics: MarketSubmissionDiagnostic[] }> {
    return this.request("/market/submissions/check", {
      method: "POST",
      body: JSON.stringify(draft),
      signal: options.signal,
    });
  }

  /** Share an endpoint's saved definition to the official market; resubmissions use the same PR while it remains unmerged. */
  static async createMarketSubmission(
    draft: MarketSubmissionDraft & { github_username: string | null },
  ): Promise<MarketSubmission> {
    return this.request("/market/submissions", { method: "POST", body: JSON.stringify(draft) });
  }

  /** Locally recorded share submissions, refreshing status once from the official service on invocation; disabled service returns 409. */
  static async listMarketSubmissions(
    options: { signal?: AbortSignal } = {},
  ): Promise<{ submissions: MarketSubmission[] }> {
    return this.request("/market/submissions", { signal: options.signal });
  }

  // ==================== Custom endpoint API ====================
  // Import/export without envelopes: request bodies and exported files are raw definition JSON, adding no wrapper fields.

  static async listCustomEndpoints(
    options: { signal?: AbortSignal } = {},
  ): Promise<{ endpoints: CustomEndpointInfo[] }> {
    return this.request("/custom-endpoints", { signal: options.signal });
  }

  static async createCustomEndpoint(definition: unknown): Promise<CustomEndpointInfo> {
    return this.request("/custom-endpoints", { method: "POST", body: JSON.stringify(definition) });
  }

  static async updateCustomEndpoint(id: number, definition: unknown): Promise<CustomEndpointInfo> {
    return this.request(`/custom-endpoints/${id}`, { method: "PUT", body: JSON.stringify(definition) });
  }

  static async deleteCustomEndpoint(id: number): Promise<void> {
    return this.request(`/custom-endpoints/${id}`, { method: "DELETE" });
  }

  /**
   * Stateless validation: save, import and diagnostic cards share one validator; always return 200 with the decision in the body.
   * @param excludeId - Exclude the existing definition on overwrite to avoid treating its own lineage as a duplicate.
   */
  static async validateCustomEndpoint(
    definition: unknown,
    options: { excludeId?: number; mediaType?: ComfyuiMediaType; signal?: AbortSignal } = {},
  ): Promise<EndpointValidateResponse> {
    const params = new URLSearchParams();
    if (options.excludeId !== undefined) params.set("exclude_id", String(options.excludeId));
    if (options.mediaType !== undefined) params.set("media_type", options.mediaType);
    const query = params.size === 0 ? "" : `?${params.toString()}`;
    return this.request(`/custom-endpoints/validate${query}`, {
      method: "POST",
      body: JSON.stringify(definition),
      signal: options.signal,
    });
  }

  /**
   * Infer workflow node-binding candidates, rematching reimports when existing bindings are supplied.
   *
   * The server retains no state: results render only the binding editor, persisting through create/full replacement
   * after user confirmation (`docs/adr/0082`). `mediaType` applies only to raw API workflows; endpoint definitions
   * already contain it.
   */
  static async inferComfyuiBindings(
    payload: unknown,
    options: { mediaType?: ComfyuiMediaType; signal?: AbortSignal } = {},
  ): Promise<ComfyuiInferResponse> {
    const query = options.mediaType === undefined ? "" : `?media_type=${options.mediaType}`;
    return this.request(`/custom-endpoints/comfyui/infer${query}`, {
      method: "POST",
      body: JSON.stringify(payload),
      signal: options.signal,
    });
  }

  /** Raw definition JSON for built-in declarative endpoints, used by Copy as mine; Python-implemented built-ins return 404. */
  static async getBuiltinEndpointDefinition(key: string, options: { signal?: AbortSignal } = {}): Promise<EndpointDefinition> {
    return this.request(`/custom-providers/endpoints/${encodeURIComponent(key)}/definition`, { signal: options.signal });
  }

  static async previewEndpointRequest(
    body: {
      definition: unknown;
      parameters: EndpointTestParameters;
      credentials?: EndpointTestCredentials;
    },
    options: { signal?: AbortSignal; assets?: EndpointTestAssets } = {},
  ): Promise<EndpointPreviewResponse> {
    return this.request(
      "/custom-endpoints/preview-request",
      endpointTestRequest(body, options.assets, options.signal),
    );
  }

  static async checkEndpointResponse(
    body: { definition: unknown; stage: EndpointTestStage; response_body: unknown },
    options: { signal?: AbortSignal } = {},
  ): Promise<EndpointStageReport> {
    return this.request("/custom-endpoints/check-response", {
      method: "POST",
      body: JSON.stringify(body),
      signal: options.signal,
    });
  }

  /** Connection test performs one actual billable generation. definition and model_ref are mutually exclusive, with exactly one required. */
  static async createTrialRun(body: {
    definition?: unknown;
    model_ref?: TrialRunModelRef;
    parameters: EndpointTestParameters;
    credentials?: EndpointTestCredentials;
  }, assets: EndpointTestAssets = {}): Promise<TrialRunInfo> {
    return this.request("/custom-endpoints/trial-runs", endpointTestRequest(body, assets));
  }

  static async getTrialRun(
    runId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<TrialRunInfo> {
    return this.request(`/custom-endpoints/trial-runs/${encodeURIComponent(runId)}`, {
      signal: options.signal,
    });
  }

  static async getTrialRunArtifact(
    runId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<Blob> {
    const endpoint = `/custom-endpoints/trial-runs/${encodeURIComponent(runId)}/artifact`;
    const response = await fetch(`${API_BASE}${endpoint}`, withAuth(endpoint, { signal: options.signal }));
    await throwIfNotOk(response, `HTTP ${response.status}`);
    return response.blob();
  }

  static async cancelTrialRun(runId: string): Promise<void> {
    return this.request(`/custom-endpoints/trial-runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    });
  }

  // ==================== Cost-estimate API ====================

  /**
   * Get project cost estimates.
   * @param projectName - Project name
   */
  static async getCostEstimate(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<CostEstimateResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/cost-estimate`, {
      signal: options.signal,
    });
  }

  // ==================== Grid image-to-video API ====================

  /**
   * Generate a Grid image, a multi-scene grid
   * @param projectName - Project name
   * @param episode - Episode number
   * @param scriptFile - Script filename
   * @param sceneIds - Optional list of specified scene IDs
   * @enqueue
   */
  static async generateGrid(
    projectName: string,
    episode: number,
    scriptFile: string,
    sceneIds?: string[]
  ): Promise<{
    success: boolean;
    grid_ids: string[];
    task_ids: string[];
    /** grid_id → task_id, containing only grids actually enqueued now. */
    task_ids_by_grid: Record<string, string>;
    /** When sceneIds is omitted, meaning generate missing grids, grids skipped because their composite image is ready but not yet split into cells. */
    unsplit_grid_ids: string[];
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/grid/${episode}`,
      { method: "POST", body: JSON.stringify({ script_file: scriptFile, scene_ids: sceneIds }) }
    );
  }

  /**
   * List all project Grid records
   * @param projectName - Project name
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
   * Get project grid-tier capabilities: 4×4/5×5 availability and maximum cells per image
   * @param projectName - Project name
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
   * Get one Grid's details
   * @param projectName - Project name
   * @param gridId - Grid ID
   */
  static async getGrid(
    projectName: string,
    gridId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<GridGeneration> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}`, {
      signal: options.signal,
    });
  }

  /**
   * Regenerate a Grid image
   * @param projectName - Project name
   * @param gridId - Grid ID
   * @enqueue
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
   * Split into cells: overwrite each storyboard cell from the current composite image, synchronously; this is the sole operation overwriting storyboard cells
   * @param projectName - Project name
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
   * Upload a replacement composite grid image, normalizing only to PNG without resizing or layout validation and without splitting
   * @param projectName - Project name
   * @param gridId - Grid ID
   * @param file - Image file
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
    return this.request<AssetListPage>(`/assets?${usp.toString()}`, options);
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
      throw new Error(typeof error.detail === "string" ? error.detail : i18n.t("errors:request_failed"));
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
      throw new Error(typeof error.detail === "string" ? error.detail : i18n.t("errors:request_failed"));
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
    if (parts.length < 3 || parts[0] !== "global_assets") return null;
    const type = parts[1];
    const filename = parts.slice(2).join("/");
    const qs = fp ? `?fp=${encodeURIComponent(fp)}` : "";
    return `${API_BASE}/global-assets/${type}/${filename}${qs}`;
  }

  // ==================== Reference-to-Video API ====================

  /** List video units for an episode on the reference-to-video path. */
  static async listReferenceVideoUnits(
    projectName: string,
    episode: number,
  ): Promise<{ units: ReferenceVideoUnit[]; unit_capabilities: ReferenceUnitCapabilityMap }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
    );
  }

  /** Create a new video unit on the reference-to-video path. */
  static async addReferenceVideoUnit(
    projectName: string,
    episode: number,
    payload: {
      prompt: string;
      duration_seconds?: number;
      note?: string | null;
      /** Insert after this unit; omission appends at the end. */
      after_unit_id?: string;
    },
  ): Promise<{ unit: ReferenceVideoUnit; unit_capability: ReferenceUnitCapability }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

  /** Patch body/duration/note on an existing unit. */
  static async patchReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
    patch: {
      prompt?: string;
      duration_seconds?: number;
      note?: string | null;
    },
  ): Promise<{ unit: ReferenceVideoUnit; unit_capability: ReferenceUnitCapability }> {
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

  /** Move `unitId` after `afterUnitId`, or first with `null`, returning the reordered unit list. */
  static async moveReferenceVideoUnit(
    projectName: string,
    episode: number,
    unitId: string,
    afterUnitId: string | null,
  ): Promise<{ units: ReferenceVideoUnit[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}/move`,
      { method: "POST", body: JSON.stringify({ after_unit_id: afterUnitId }) },
    );
  }

  /**
   * Pre-enqueue duration-tier preflight: confirm with users first when requested seconds differ from the request-duration basis.
   *
   * Resolve against project/script/asset state at request time; workers reproject current state when starting.
   */
  static async precheckReferenceVideoDuration(
    projectName: string,
    episode: number,
    unitId: string,
    options?: { signal?: AbortSignal },
  ): Promise<ReferenceDurationPrecheck> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}/duration-precheck`,
      { signal: options?.signal },
    );
  }

  /**
   * Read-time derived video-unit body preview: utterances and visible downgrade hints.
   *
   * Read-only without persistence; the body remains authoritative. The backend localizes hints by request language, including audio-related
   * ones dependent on current project video-model capabilities; the frontend does not translate again.
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

  static async previewReferenceUnitPrompt(
    projectName: string,
    episode: number,
    unitId: string,
    prompt: string,
    options?: { signal?: AbortSignal },
  ): Promise<ReferenceUnitPromptPreview> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/${encodeURIComponent(unitId)}/prompt-preview`,
      { method: "POST", body: JSON.stringify({ prompt }), signal: options?.signal },
    );
  }

  /**
   * Enqueue generation; returns 202 with task_id.
   * @enqueue
   */
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

  /** Batch admission preview: same decision and estimated costs as submission, without creating tasks, supplying confirmation-dialog targets. */
  static async previewReferenceVideoBatch(
    projectName: string,
    episode: number,
    payload: ReferenceBatchGenerateRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<ReferenceBatchPreview> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/generate-batch/preview`,
      { method: "POST", body: JSON.stringify(payload), signal: options.signal },
    );
  }

  /**
   * All-or-nothing batch admission: evaluate every target unit in one request.
   *
   * Always return 200, with the outcome in `decision`; only `admitted` creates tasks.
   * `confirmation_required` / `blocked` create none, so follow the decision before choosing next steps.
   * @enqueue
   */
  static async generateReferenceVideoBatch(
    projectName: string,
    episode: number,
    payload: ReferenceBatchGenerateRequest,
  ): Promise<ReferenceBatchAdmission> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units/generate-batch`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

}

export { API };
