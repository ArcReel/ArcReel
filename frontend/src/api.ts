
import type {
  CharacterDerivativeStatus,
  ItemLocationsResponse,
  ProjectData,
  ProjectSummary,
  WorkspaceResponse,
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
  TaskProgress,
  AssistantSessionResumedPayload,
  AutoBackendsResponse,
  RecommendationChange,
  RecommendationChangesResponse,
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
  SavedObjectStorageConnection,
  ObjectStorageCleanupResult,
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
  AuthorPromptsRequest,
  AuthorPromptsResponse,
  PlanScriptRequest,
  PlanScriptResponse,
  GenerateAdScriptRequest,
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
import type { Asset, AssetType, AssetCreatePayload, AssetIdentity, AssetListPage, AssetUpdatePayload } from "@/types/asset";
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


export type FileUrlWidth = 160 | 320 | 640 | 1280;

export interface FileUrlOptions {
  width?: FileUrlWidth;
}

const ASSET_TYPE_PATH: Record<ProjectAssetType, string> = {
  character: "characters",
  scene: "scenes",
  prop: "props",
  product: "products",
};

export const CHARACTER_DERIVATIVE_RESOURCE_TYPE = "character_derivatives";

export function derivativeResourceId(characterName: string, derivativeName: string): string {
  return `${characterName}/${derivativeName}`;
}

function encodePath(value: string): string {
  return value.split("/").map(encodeURIComponent).join("/");
}

function derivativesPath(projectName: string, charName: string): string {
  return `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(charName)}/derivatives`;
}

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

function agentMemoryBase(scope: AgentMemoryScope): string {
  return scope.level === "user"
    ? "/agent/memory"
    : `/projects/${encodeURIComponent(scope.projectName)}/agent-memory`;
}


class API {
  static async request<T = unknown>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    return requestJson<T>(endpoint, options);
  }


  static async getSystemConfig(options: { signal?: AbortSignal } = {}): Promise<GetSystemConfigResponse> {
    return this.request("/system/config", { signal: options.signal });
  }

  static async getModelCandidates(
    options: { signal?: AbortSignal } = {}
  ): Promise<ModelCandidatesResponse> {
    return this.request("/system/config/model-candidates", { signal: options.signal });
  }

  static async getAutoBackends(options: { signal?: AbortSignal } = {}): Promise<AutoBackendsResponse> {
    return this.request("/system/config/auto-backends", { signal: options.signal });
  }

  static async getRecommendationChanges(
    options: { signal?: AbortSignal } = {},
  ): Promise<RecommendationChangesResponse> {
    return this.request("/system/config/recommendation-changes", { signal: options.signal });
  }

  static async acknowledgeRecommendation(change: RecommendationChange, apply: boolean): Promise<void> {
    await this.request("/system/config/recommendation-changes/acknowledge", {
      method: "POST",
      body: JSON.stringify({ media_type: change.media_type, recommended: change.recommended, apply }),
    });
  }

  static async getNarrationDefaults(
    options: { signal?: AbortSignal } = {},
  ): Promise<NarrationDefaultsResponse> {
    return this.request("/system/global-narration-settings", { signal: options.signal });
  }

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


  static async listPromptTemplates(
    options: { signal?: AbortSignal } = {}
  ): Promise<PromptTemplateListResponse> {
    return this.request("/prompt-templates", { signal: options.signal });
  }

  static async getPromptTemplate(
    templateId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<PromptTemplateDetail> {
    const path = templateId.split("/").map(encodeURIComponent).join("/");
    return this.request(`/prompt-templates/${path}`, { signal: options.signal });
  }

  static async getPromptPartial(
    name: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<PromptTemplatePartial> {
    const path = name.split("/").map(encodeURIComponent).join("/");
    return this.request(`/prompt-templates/partials/${path}`, { signal: options.signal });
  }


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

  static async getWorkspace(
    name: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<WorkspaceResponse> {
    return this.request(`/projects/${encodeURIComponent(name)}/workspace`, { signal: options.signal });
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

  static async locateItems(
    projectName: string,
    itemIds: string[],
    options: { signal?: AbortSignal } = {}
  ): Promise<ItemLocationsResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/item-locations`, {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds }),
      signal: options.signal,
    });
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



  static async getFileView(
    projectName: string,
    path: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<FileViewListing> {
    const query = new URLSearchParams({ path }).toString();
    return this.request(`/projects/${encodeURIComponent(projectName)}/file-view?${query}`, { signal: options.signal });
  }

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

  static async deleteFileViewEntry(projectName: string, path: string): Promise<{ record: TrashRecord }> {
    const query = new URLSearchParams({ path }).toString();
    return this.request(`/projects/${encodeURIComponent(projectName)}/file-view?${query}`, { method: "DELETE" });
  }


  static async getTrash(projectName: string, options: { signal?: AbortSignal } = {}): Promise<TrashListing> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/trash`, { signal: options.signal });
  }

  static async restoreTrashRecord(projectName: string, recordId: string): Promise<RestoreResult> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/trash/${encodeURIComponent(recordId)}/restore`,
      { method: "POST" },
    );
  }

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

  static async getAgentMemory(
    scope: AgentMemoryScope,
    options: { signal?: AbortSignal } = {}
  ): Promise<AgentMemoryOverview> {
    return this.request(agentMemoryBase(scope), { signal: options.signal });
  }

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

  static async restoreDeletedProject(id: string): Promise<{ name: string }> {
    return this.request(`/deleted-projects/${encodeURIComponent(id)}/restore`, { method: "POST" });
  }

  static async purgeDeletedProject(id: string): Promise<{ success: boolean }> {
    return this.request(`/deleted-projects/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  static async getVideoCapabilities(
    name: string,
    options: VideoCapabilitiesQuery = {}
  ): Promise<VideoCapabilities> {
    return this.request(
      `/projects/${encodeURIComponent(name)}/video-capabilities${videoCapabilitiesQuery(options)}`,
      { signal: options.signal },
    );
  }

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


  static async createEditTimeline(projectName: string, episode: number, name: string): Promise<EditTimelineReadout> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/edit-timelines`, {
      method: "POST",
      body: JSON.stringify({ from: "script", name }),
    });
  }

  private static editTimelinePath(projectName: string, timelineId: string): string {
    return `/projects/${encodeURIComponent(projectName)}/edit-timelines/${encodeURIComponent(timelineId)}`;
  }

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
   * 入队渲染成片（最新修订）。组件经 actions/render 调用。
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
   * 入队导出剪映草稿（最新修订，旁白版本省略时按项目默认）。组件经 actions/render 调用。
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

  static async deleteEditTimeline(projectName: string, timelineId: string): Promise<void> {
    await this.request(
      this.editTimelinePath(projectName, timelineId),
      { method: "DELETE" },
    );
  }

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

  static async uploadBgm(projectName: string, file: File): Promise<{ success: boolean; bgm: ProjectBgm }> {
    return API.postFileUpload(`/projects/${encodeURIComponent(projectName)}/upload/bgm`, file);
  }

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

  static async getCharacterDerivativeSheets(
    projectName: string,
    charName: string,
    options?: { signal?: AbortSignal }
  ): Promise<{ success: boolean; derivatives: Record<string, CharacterDerivativeStatus> }> {
    return this.request(derivativesPath(projectName, charName), { signal: options?.signal });
  }

  /**
   * 提交一次衍生资产图生成。指令由衍生自己的外观变化描述加固定守卫构成，请求体没有 prompt。
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


  static async getAssetSheetStatus(
    projectName: string,
    options?: { signal?: AbortSignal; include?: "derivative_references" }
  ): Promise<{ assets: AssetSheetStatusRow[] }> {
    const query = options?.include ? `?include=${encodeURIComponent(options.include)}` : "";
    return this.request(`/projects/${encodeURIComponent(projectName)}/asset-sheets/status${query}`, {
      signal: options?.signal,
    });
  }

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
   * 提交一批，立即返回成员任务；整批终态由调用方按任务跟踪。
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
   * 提交一集的分镜图或分镜视频批量；分镜视频整批准入未通过时不建任务，返回结论。
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


  static async getScript(
    projectName: string,
    scriptFile: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<EpisodeScriptSnapshot> {
    const normalized = scriptFile.replace(/^scripts\//, "");
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scripts/${encodeURIComponent(normalized)}`,
      { signal: options.signal }
    );
  }

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
   * 提示词编写（「AI 编写 / AI 重写」），与 Agent 的 generate_episode_script 同一服务命令；提交即返生成批次。
   * 显式重写会覆盖已有内容而未带有效 `overwrite_revision` 时 409，`diagnostic.prompt_overwrite` 带丢失清单。
   * 附加指令随请求按集保存。
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
   * AI 规划脚本，与 Agent 的 generate_script_plan 同一服务命令；提交即返生成批次。
   * 新的脚本规划整份替换本集现有的规划与草稿，替换前的确认由调用方负责。附加指令随请求按集保存。
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
   * 广告/短片「AI 生成脚本」，与 Agent 的 generate_episode_script 同一服务命令；提交即返生成批次，结果直接写成正式脚本。
   * 整份重做（`regenerate`）会替换已有正式脚本而未带有效 `overwrite_revision` 时 409，`diagnostic.script_overwrite` 带丢失清单。
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

  static async startBlankScript(projectName: string, episode: number): Promise<SuccessResponse & { script_file: string }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/blank-script`, {
      method: "POST",
    });
  }

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


  static async uploadFile(
    projectName: string,
    uploadType: string,
    file: File,
    name: string | null = null,
    options: {
      onConflict?: "fail" | "replace" | "rename";
      role?: "whole_source" | "episode";
      insertAt?: number;
      sourceKind?: SourceKind;
      revision?: string;
      signal?: AbortSignal;
    } = {}
  ): Promise<{
    success: boolean;
    path?: string;
    status?: SourceFileChangeResponse["status"];
    impact?: SourceFileChangeResponse["impact"];
    revision?: string;
    url?: string;
    filename?: string;
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

    if (uploadType === "source" && response.status === 409) {
      let detail: { existing?: string; suggested_name?: string; message?: string } | null = null;
      try {
        const body = (await response.json()) as { detail?: { existing?: string; suggested_name?: string; message?: string } };
        detail = body?.detail ?? null;
      } catch {
        /* ignore */
      }
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

  private static async postFileUpload<T>(url: string, file: File): Promise<T> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${API_BASE}${url}`, withAuth(url, { method: "POST", body: formData }));
    await throwIfNotOk(response, i18n.t("errors:upload_failed"));
    return (await response.json()) as T;
  }

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

  static async getWorkflowStatus(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<WorkflowStatus> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/workflow-status`, {
      signal: options.signal,
    });
  }

  static async getEpisodeNextSteps(
    projectName: string,
    options: { signal?: AbortSignal; episodes?: number[]; cursor?: string } = {}
  ): Promise<{ episodes: EpisodeNextStep[]; next_cursor: string | null }> {
    const params = new URLSearchParams();
    for (const episode of options.episodes ?? []) params.append("episodes", String(episode));
    if (options.cursor !== undefined) params.set("cursor", options.cursor);
    const query = params.size ? `?${params}` : "";
    return this.request(`/projects/${encodeURIComponent(projectName)}/workflow-status/episodes${query}`, {
      signal: options.signal,
    });
  }

  static async getEpisodeEditOverview(
    projectName: string,
    episode: number,
    options: { signal?: AbortSignal } = {}
  ): Promise<EpisodeEditOverview> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/edit-overview`, {
      signal: options.signal,
    });
  }

  static async retryProjectMigration(projectName: string): Promise<{ success: boolean }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/migration/retry`, {
      method: "POST",
    });
  }

  static async acknowledgeMigrationReport(
    projectName: string,
    migratedAt: string,
  ): Promise<{ success: boolean; acknowledged: boolean }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/migration-report/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ migrated_at: migratedAt }),
    });
  }

  static getFileUrl(
    projectName: string,
    path: string,
    cacheBust: number | string | null,
    options: FileUrlOptions = {}
  ): string {
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

  static async getEpisodesView(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<EpisodesView> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes-view`, {
      signal: options.signal,
    });
  }

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
   * AI 规划分集：从规划起点逐窗规划到整本源文结尾，每一窗是一个排队的文本任务；提交即返首窗的生成批次。
   * 已有进行中的分集规划时 409，没有整本源文等准入不成立时 422。附加指令不写进项目。
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

  static async previewEpisodeReplan(projectName: string, episode: number): Promise<ReplanPreview> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan`, {
      method: "POST",
      body: JSON.stringify({ episode, dry_run: true }),
    });
  }

  /**
   * 从这一集开始重新规划：逐窗生成一份「新的分集方案」，分集账本在采纳前不变；提交即返首窗的生成批次。
   * 已有方案或分集规划在进行时 409。附加指令随方案保存。
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
   * 接着生成中途停止的新的分集方案：从方案的结尾逐窗生成到整本源文结尾，沿用发起时的附加指令；提交即返首窗的
   * 生成批次。方案已覆盖到结尾或已过时时 409，分集规划在进行时 409。
   * @enqueue
   */
  static async continueEpisodeReplan(projectName: string, candidateId: string): Promise<EpisodePlanningResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan/continue`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  }

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

  static async discardEpisodeReplan(projectName: string, candidateId: string): Promise<void> {
    await this.request(`/projects/${encodeURIComponent(projectName)}/episode-replan/discard`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  }

  static async createEpisode(projectName: string, body: CreateEpisodeBody): Promise<{ episode: number }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episodes`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  static async moveEpisode(projectName: string, episode: number, after: number | null): Promise<void> {
    await this.request(`/projects/${encodeURIComponent(projectName)}/episodes/${episode}/move`, {
      method: "POST",
      body: JSON.stringify({ after }),
    });
  }

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

  static async stopEpisodePlanning(projectName: string): Promise<StopEpisodePlanningResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/episode-planning/stop`, { method: "POST" });
  }

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

  static async deleteCharacterReferenceAudio(
    projectName: string,
    characterName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/characters/${encodeURIComponent(characterName)}/reference-audio`,
      { method: "DELETE" }
    );
  }


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


  /**
   * 生成分镜图
   * @param projectName - 项目名称
   * @param segmentId - 分镜 ID
   * @param prompt - 图片生成 prompt（支持字符串或结构化对象）
   * @param scriptFile - 剧本文件名
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
   * 生成视频
   * @param projectName - 项目名称
   * @param segmentId - 分镜 ID
   * @param prompt - 视频生成 prompt（支持字符串或结构化对象）
   * @param scriptFile - 剧本文件名
   * @param durationSeconds - 时长（秒）
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
   * 生成单段旁白配音（文本由后端从剧本 novel_text 读取）
   * @param projectName - 项目名称
   * @param segmentId - 分镜 ID
   * @param scriptFile - 剧本文件名
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
   * 批量生成全集旁白配音（只入队缺少旁白且有原文的段）
   * @param projectName - 项目名称
   * @param scriptFile - 剧本文件名
   * @enqueue
   */
  static async generateEpisodeNarrationAudio(
    projectName: string,
    scriptFile: string
  ): Promise<{
    success: boolean;
    task_ids: string[];
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
   * 生成角色资产图
   * @param projectName - 项目名称
   * @param charName - 角色名称
   *
   * 请求体没有 prompt：描述只取项目里存储的条目。
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
   * 生成场景资产图
   * @param projectName - 项目名称
   * @param sceneName - 场景名称
   *
   * 请求体没有 prompt：描述只取项目里存储的条目。
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
   * 生成道具资产图
   * @param projectName - 项目名称
   * @param propName - 道具名称
   *
   * 请求体没有 prompt：描述只取项目里存储的条目。
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
   * 生成商品资产图（product sheet）
   * @param projectName - 项目名称
   * @param productName - 商品名称
   *
   * 请求体没有 prompt：描述只取项目里存储的条目。
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
   * 提交图片指令式编辑任务：以当前图为唯一底图、指令为唯一 prompt 走 i2i，
   * 新图覆盖 current、旧图进版本历史。分镜（resourceType="storyboard"）须带 scriptFile。
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


  static async getTask(taskId: string, options: { signal?: AbortSignal } = {}): Promise<TaskItem> {
    const { task } = await this.request<{ task: TaskItem }>(
      `/tasks/${encodeURIComponent(taskId)}`,
      { signal: options.signal },
    );
    return task;
  }

  static getActiveTasks(
    projectName: string | null,
    options: { signal?: AbortSignal } = {},
  ): Promise<{ items: TaskItem[]; truncated: boolean }> {
    const query = projectName ? "?" + new URLSearchParams({ project_name: projectName }).toString() : "";
    return this.request(`/tasks/active${query}`, { signal: options.signal });
  }

  static lookupTasks(taskIds: string[], projectName: string | null, signal: AbortSignal): Promise<{ items: TaskItem[]; missing_ids: string[] }> {
    return this.request("/tasks/lookup", {
      method: "POST", signal, body: JSON.stringify({ task_ids: taskIds, project_name: projectName }),
    });
  }

  static async listTasks(
    filters: TaskListFilters = {},
    options: { signal?: AbortSignal } = {},
  ): Promise<{ items: TaskItem[]; total: number; page: number; page_size: number }> {
    const params = new URLSearchParams();
    if (filters.projectName) params.append("project_name", filters.projectName);
    if (filters.status) params.append("status", filters.status);
    if (filters.resourceId) params.append("resource_id", filters.resourceId);
    if (filters.taskType) params.append("task_type", filters.taskType);
    if (filters.source) params.append("source", filters.source);
    if (filters.page) params.append("page", String(filters.page));
    if (filters.pageSize) params.append("page_size", String(filters.pageSize));
    const query = params.toString();
    return this.request(`/tasks${query ? "?" + query : ""}`, { signal: options.signal });
  }

  static async listProjectTasks(
    projectName: string,
    filters: Omit<TaskListFilters, "projectName"> = {}
  ): Promise<{ items: TaskItem[]; total: number; page: number; page_size: number }> {
    const params = new URLSearchParams();
    if (filters.status) params.append("status", filters.status);
    if (filters.resourceId) params.append("resource_id", filters.resourceId);
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
    projectName: string | null = null,
    options: { signal?: AbortSignal } = {},
  ): Promise<{ stats: TaskStats }> {
    const params = new URLSearchParams();
    if (projectName) params.append("project_name", projectName);
    const query = params.toString();
    return this.request(`/tasks/stats${query ? "?" + query : ""}`, { signal: options.signal });
  }


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
          case "task_progress":
            options.onTaskProgress?.(payload as unknown as TaskProgress);
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

  static getAssistantEntriesStreamUrl(
    projectName: string,
    sessionId: string,
    after: number = -1
  ): string {
    const base = `${API_BASE}${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/entries/stream`;
    return after >= 0 ? `${base}?after=${after}` : base;
  }

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


  static async getUsageRecords(
    query: UsageRecordsQuery = {},
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageRecordPage> {
    const params = new URLSearchParams();
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

  static async getUsageSession(
    sessionId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageAgentSessionDetail> {
    return this.request(`/usage/sessions/${encodeURIComponent(sessionId)}`, {
      signal: options.signal,
    });
  }

  static async getUsageRecord(
    recordId: number,
    options: { signal?: AbortSignal } = {}
  ): Promise<UsageRecordDetail> {
    return this.request(`/usage/records/${recordId}`, { signal: options.signal });
  }

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


  static async listApiKeys(options: { signal?: AbortSignal } = {}): Promise<ApiKeyInfo[]> {
    return this.request("/api-keys", { signal: options.signal });
  }

  static async createApiKey(name: string, expiresDays?: number): Promise<CreateApiKeyResponse> {
    return this.request("/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, expires_days: expiresDays ?? null }),
    });
  }

  static async deleteApiKey(keyId: number): Promise<void> {
    return this.request(`/api-keys/${keyId}`, { method: "DELETE" });
  }


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

  static async getNewEntryHints(
    query: { name: string; mediaType: MediaType },
    options: { signal?: AbortSignal } = {}
  ): Promise<NewEntryHints> {
    const params = new URLSearchParams({ name: query.name, media_type: query.mediaType });
    return this.request(`/catalog/new-entry-hints?${params}`, { signal: options.signal });
  }

  static async createCatalogEntry(entry: NewCatalogEntry): Promise<CatalogEntryHit> {
    return this.request("/catalog/entries", { method: "POST", body: JSON.stringify(entry) });
  }


  static async getProviders(
    options: { signal?: AbortSignal } = {}
  ): Promise<{ providers: ProviderInfo[] }> {
    return this.request("/providers", { signal: options.signal });
  }

  static async getProviderConfig(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderConfigDetail> {
    return this.request(`/providers/${encodeURIComponent(id)}/config`, { signal: options.signal });
  }

  static async patchProviderConfig(
    id: string,
    patch: Record<string, string | null>
  ): Promise<void> {
    return this.request(`/providers/${encodeURIComponent(id)}/config`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  static async getProviderMultiplier(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderMultiplierInfo> {
    return this.request(`/providers/${encodeURIComponent(id)}/multiplier`, { signal: options.signal });
  }

  static async setProviderMultiplier(id: string, multiplier: number | null): Promise<ProviderMultiplierInfo> {
    return this.request(`/providers/${encodeURIComponent(id)}/multiplier`, {
      method: "PUT",
      body: JSON.stringify({ multiplier }),
    });
  }

  static async listProviderModels(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderModelsResponse> {
    return this.request(`/providers/${encodeURIComponent(id)}/models`, { signal: options.signal });
  }

  static async discoverProviderModels(
    id: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<FetchProviderModelsResponse> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/discover`, {
      method: "POST",
      signal: options.signal,
    });
  }

  static async addProviderModel(id: string, body: AddProviderModelRequest): Promise<AddProviderModelResponse> {
    return this.request(`/providers/${encodeURIComponent(id)}/models`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  static async setProviderModelEnabled(id: string, modelId: string, enabled: boolean): Promise<ProviderModelItem> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/enabled`, {
      method: "PUT",
      body: JSON.stringify({ model_id: modelId, enabled }),
    });
  }

  static async getProviderModel(
    id: string,
    modelId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderModelDetail> {
    return this.request(`/providers/${encodeURIComponent(id)}/models/${encodePath(modelId)}`, {
      signal: options.signal,
    });
  }

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

  static async restoreCatalogEntry(key: CatalogEntryKey): Promise<void> {
    await this.request("/catalog/entries/restore-official", { method: "POST", body: JSON.stringify(key) });
  }

  static async removeProviderModel(id: string, modelId: string): Promise<void> {
    await this.request(`/providers/${encodeURIComponent(id)}/models/${encodePath(modelId)}`, { method: "DELETE" });
  }

  static async getProviderModelReferences(
    id: string,
    modelId: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<ProviderModelReferences> {
    const params = new URLSearchParams({ model_id: modelId });
    return this.request(`/providers/${encodeURIComponent(id)}/model-references?${params}`, { signal: options.signal });
  }

  static async checkProviderConnectivity(id: string, credentialId?: number): Promise<ConnectivityCheckResult> {
    const params = credentialId != null ? `?credential_id=${credentialId}` : "";
    return this.request(`/providers/${encodeURIComponent(id)}/test${params}`, {
      method: "POST",
    });
  }


  static async getObjectStorageConnection(options: { signal?: AbortSignal } = {}): Promise<ObjectStorageSettings> {
    return this.request("/object-storage/connection", { signal: options.signal });
  }

  static async saveNewImageTarget(target: NewImageTarget): Promise<{ target: NewImageTarget }> {
    return this.request("/object-storage/new-image-target", { method: "PUT", body: JSON.stringify({ target }) });
  }

  static async saveObjectStorageConnection(data: SaveObjectStorageConnectionPayload): Promise<SavedObjectStorageConnection> {
    return this.request("/object-storage/connection", { method: "PUT", body: JSON.stringify(data) });
  }

  static async deleteObjectStorageConnection(deleteRemoteObjects = true): Promise<ObjectStorageCleanupResult> {
    return this.request(`/object-storage/connection?delete_remote_objects=${deleteRemoteObjects}`, { method: "DELETE" });
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

  static async uploadToProviderAssetLibrary(
    projectName: string,
    body: { resource_type: string; resource_id: string; account_id: number },
  ): Promise<ProviderAssetLink> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  static async importProviderAsset(
    projectName: string,
    body: { resource_type: string; resource_id: string; account_id: number; asset_id: string },
  ): Promise<ProviderAssetImportResult> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/provider-asset-links/import`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

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


  static async listMarketSources(
    options: { signal?: AbortSignal } = {},
  ): Promise<MarketSourceListResponse> {
    return this.request("/market/sources", { signal: options.signal });
  }

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

  static async deleteMarketSource(id: number): Promise<void> {
    return this.request(`/market/sources/${id}`, { method: "DELETE" });
  }

  static async reorderMarketSources(ids: number[]): Promise<MarketSourceListResponse> {
    return this.request("/market/sources/order", { method: "PUT", body: JSON.stringify({ ids }) });
  }

  static async refreshMarketSource(id: number): Promise<MarketSourceInfo> {
    return this.request(`/market/sources/${id}/refresh`, { method: "POST" });
  }

  static async refreshMarketSources(
    options: { staleOnly?: boolean; signal?: AbortSignal } = {},
  ): Promise<MarketSourceListResponse> {
    const query = options.staleOnly ? "?stale_only=true" : "";
    return this.request(`/market/refresh${query}`, { method: "POST", signal: options.signal });
  }

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

  static async installMarketEntry(sourceId: number, slug: string, definitionDigest: string, overwriteEndpointId?: number): Promise<{ endpoint: CustomEndpointInfo; installation: MarketEntryInstallation }> {
    return this.request(`/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}/install`, {
      method: "POST", body: JSON.stringify({ definition_digest: definitionDigest, overwrite_endpoint_id: overwriteEndpointId }),
    });
  }

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


  static async getOfficialService(options: { signal?: AbortSignal } = {}): Promise<OfficialServiceState> {
    return this.request("/official-service", { signal: options.signal });
  }

  static async updateOfficialService(patch: {
    enabled?: boolean;
    notice_seen?: boolean;
  }): Promise<OfficialServiceState> {
    return this.request("/official-service", { method: "PATCH", body: JSON.stringify(patch) });
  }

  static async resetOfficialInstanceId(): Promise<OfficialServiceState> {
    return this.request("/official-service/instance-id/reset", { method: "POST" });
  }

  static async listMarketEntryAggregates(
    options: { signal?: AbortSignal } = {},
  ): Promise<{ items: MarketEntryAggregate[] }> {
    return this.request("/market/entries/aggregates?type=endpoint", { signal: options.signal });
  }

  static async rateMarketEntry(sourceId: number, slug: string, stars: number): Promise<void> {
    return this.request(`/market/sources/${sourceId}/entries/${encodeURIComponent(slug)}/rating`, {
      method: "PUT",
      body: JSON.stringify({ stars }),
    });
  }

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

  static async createMarketSubmission(
    draft: MarketSubmissionDraft & { github_username: string | null },
  ): Promise<MarketSubmission> {
    return this.request("/market/submissions", { method: "POST", body: JSON.stringify(draft) });
  }

  static async listMarketSubmissions(
    options: { signal?: AbortSignal } = {},
  ): Promise<{ submissions: MarketSubmission[] }> {
    return this.request("/market/submissions", { signal: options.signal });
  }


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


  static async getCostEstimate(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<CostEstimateResponse> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/cost-estimate`, {
      signal: options.signal,
    });
  }


  /**
   * 生成 Grid 图像（多场景网格）
   * @param projectName - 项目名称
   * @param episode - 剧集编号
   * @param scriptFile - 剧本文件名
   * @param sceneIds - 可选，指定场景 ID 列表
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
    blocked?: Array<{ scene_id: string; code: string; params: Record<string, unknown>; message: string }>;
    task_ids: string[];
    task_ids_by_grid: Record<string, string>;
    unsplit_grid_ids: string[];
    deduped: boolean;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/grid/${episode}`,
      { method: "POST", body: JSON.stringify({ script_file: scriptFile, scene_ids: sceneIds }) }
    );
  }

  static async listGrids(
    projectName: string,
    options: { signal?: AbortSignal } = {}
  ): Promise<GridGeneration[]> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/grids`, {
      signal: options.signal,
    });
  }

  static async getGridCapability(
    projectName: string,
    options: { signal?: AbortSignal; episode?: number; scriptFile?: string } = {}
  ): Promise<GridCapability> {
    const query = options.episode && options.scriptFile ? `?${new URLSearchParams({ episode: String(options.episode), script_file: options.scriptFile })}` : "";
    return this.request(`/projects/${encodeURIComponent(projectName)}/grid-capability${query}`, {
      signal: options.signal,
    });
  }

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
   * 重新生成 Grid 图像
   * @param projectName - 项目名称
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

  static async uploadGridImage(
    projectName: string,
    gridId: string,
    file: File
  ): Promise<{ success: boolean; path: string; version: number; asset_fingerprints: Record<string, number> }> {
    const url = `/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}/upload`;
    return API.postFileUpload(url, file);
  }


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

  static async findAssetByName(type: AssetType, name: string, signal: AbortSignal) {
    const query = new URLSearchParams({ type, name });
    return this.request<{ asset: AssetIdentity | null }>(`/assets/by-name?${query}`, { signal });
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
    overwrite_asset_id?: string;
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
      failed: Array<{ id: string; reason: string; message?: string }>;
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


  static async listReferenceVideoUnits(
    projectName: string,
    episode: number,
  ): Promise<{ units: ReferenceVideoUnit[]; unit_capabilities: ReferenceUnitCapabilityMap }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
    );
  }

  static async addReferenceVideoUnit(
    projectName: string,
    episode: number,
    payload: {
      prompt: string;
      duration_seconds?: number;
      note?: string | null;
      after_unit_id?: string;
    },
  ): Promise<{ unit: ReferenceVideoUnit; unit_capability: ReferenceUnitCapability }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/reference-videos/episodes/${episode}/units`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

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
   * 批量生成的全有或全无准入：一次请求评估全部目标单元。
   *
   * 恒返回 200——`decision` 携带结局，只有 `admitted` 建了任务；
   * `confirmation_required` 与 `blocked` 一个任务也没建，须按结论再决定下一步。
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
