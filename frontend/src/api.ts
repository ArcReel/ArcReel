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
  EpisodeScript,
  TaskItem,
  TaskStats,
  SessionMeta,
  AssistantSnapshot,
  SkillInfo,
  ProjectOverview,
  ProjectChangeBatchPayload,
  ProjectEventSnapshotPayload,
  GetSystemConfigResponse,
  SystemConfigPatch,
  SystemConnectionTestRequest,
  SystemConnectionTestResponse,
  ApiKeyInfo,
  CreateApiKeyResponse,
} from "@/types";
import { getToken, clearToken } from "@/utils/auth";

// ==================== Helper types ====================

/** Version metadata returned by the versions API. */
export interface VersionInfo {
  version: number;
  filename: string;
  created_at: string;
  file_size: number;
  is_current: boolean;
  file_url?: string;
  prompt?: string;
  restored_from?: number;
}

/** Options for {@link API.openTaskStream}. */
export interface TaskStreamOptions {
  projectName?: string;
  lastEventId?: number | string;
  onSnapshot?: (payload: TaskStreamSnapshotPayload, event: MessageEvent) => void;
  onTask?: (payload: TaskStreamTaskPayload, event: MessageEvent) => void;
  onError?: (event: Event) => void;
}

export interface TaskStreamSnapshotPayload {
  tasks: TaskItem[];
  stats: TaskStats;
}

export interface TaskStreamTaskPayload {
  action: "created" | "updated";
  task: TaskItem;
  stats: TaskStats;
}

export interface ProjectEventStreamOptions {
  projectName: string;
  onSnapshot?: (payload: ProjectEventSnapshotPayload, event: MessageEvent) => void;
  onChanges?: (payload: ProjectChangeBatchPayload, event: MessageEvent) => void;
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

/** Draft metadata returned by listDrafts. */
export interface DraftInfo {
  episode: number;
  step: number;
  filename: string;
  modified_at: string;
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
      .catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || fallbackMsg);
  }
}

function handleUnauthorized(response: Response): void {
  if (response.status !== 401) return;

  clearToken();
  globalThis.location.href = "/login";
  throw new Error("认证已过期，请重新登录");
}

/** 为 fetch options 注入 Authorization header */
function withAuth(options: RequestInit = {}): RequestInit {
  const token = getToken();
  if (!token) return options;
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
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

    const response = await fetch(url, withAuth({ ...defaultOptions, ...options }));

    if (!response.ok) {
      handleUnauthorized(response);
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || "请求失败");
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return response.json();
  }

  // ==================== 系统配置 ====================

  static async getSystemConfig(): Promise<GetSystemConfigResponse> {
    return this.request("/system/config");
  }

  static async updateSystemConfig(
    patch: SystemConfigPatch,
  ): Promise<GetSystemConfigResponse> {
    return this.request("/system/config", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  static async uploadVertexCredentials(
    file: File,
  ): Promise<GetSystemConfigResponse> {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(
      `${API_BASE}/system/config/vertex-credentials`,
      withAuth({
        method: "POST",
        body: formData,
      }),
    );

    await throwIfNotOk(response, "上传失败");
    return response.json();
  }

  static async testSystemConnection(
    payload: SystemConnectionTestRequest,
  ): Promise<SystemConnectionTestResponse> {
    return this.request("/system/config/connection-test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  // ==================== 项目管理 ====================

  static async listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return this.request("/projects");
  }

  static async createProject(
    title: string,
    style: string = "",
    contentMode: string = "narration"
  ): Promise<{ success: boolean; name: string; project: ProjectData }> {
    return this.request("/projects", {
      method: "POST",
      body: JSON.stringify({
        title,
        style,
        content_mode: contentMode,
      }),
    });
  }

  static async getProject(
    name: string
  ): Promise<{
    project: ProjectData;
    scripts: Record<string, EpisodeScript>;
    asset_fingerprints?: Record<string, number>;
  }> {
    return this.request(`/projects/${encodeURIComponent(name)}`);
  }

  static async updateProject(
    name: string,
    updates: Partial<ProjectData>
  ): Promise<{ success: boolean; project: ProjectData }> {
    if ("content_mode" in updates || "aspect_ratio" in updates) {
      throw new Error("项目创建后不支持修改 content_mode 或 aspect_ratio");
    }
    return this.request(`/projects/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    });
  }

  static async deleteProject(name: string): Promise<SuccessResponse> {
    return this.request(`/projects/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  }

  static async requestExportToken(
    projectName: string
  ): Promise<{ download_token: string; expires_in: number }> {
    return this.request(`/projects/${encodeURIComponent(projectName)}/export/token`, {
      method: "POST",
    });
  }

  static getExportDownloadUrl(
    projectName: string,
    downloadToken: string,
    scope: "full" | "current" = "full"
  ): string {
    return `${API_BASE}/projects/${encodeURIComponent(projectName)}/export?download_token=${encodeURIComponent(downloadToken)}&scope=${encodeURIComponent(scope)}`;
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
      withAuth({
        method: "POST",
        body: formData,
      })
    );

    if (!response.ok) {
      handleUnauthorized(response);

      const payload = await response
        .json()
        .catch(() => ({ detail: response.statusText, errors: [], warnings: [] }));
      const error = new Error(
        typeof payload.detail === "string" ? payload.detail : "导入失败"
      ) as Error & {
        status?: number;
        detail?: string;
        errors?: string[];
        warnings?: string[];
        conflict_project_name?: string;
      };
      error.status = response.status;
      error.detail = typeof payload.detail === "string" ? payload.detail : "导入失败";
      error.errors = Array.isArray(payload.errors) ? payload.errors : [];
      error.warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      if (typeof payload.conflict_project_name === "string") {
        error.conflict_project_name = payload.conflict_project_name;
      }
      throw error;
    }

    return response.json();
  }

  // ==================== 人物管理 ====================

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

  // ==================== 线索管理 ====================

  static async addClue(
    projectName: string,
    name: string,
    clueType: string,
    description: string,
    importance: string = "major"
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/clues`,
      {
        method: "POST",
        body: JSON.stringify({
          name,
          clue_type: clueType,
          description,
          importance,
        }),
      }
    );
  }

  static async updateClue(
    projectName: string,
    clueName: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/clues/${encodeURIComponent(clueName)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
    );
  }

  static async deleteClue(
    projectName: string,
    clueName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/clues/${encodeURIComponent(clueName)}`,
      {
        method: "DELETE",
      }
    );
  }

  // ==================== 场景管理 ====================

  static async getScript(
    projectName: string,
    scriptFile: string
  ): Promise<EpisodeScript> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scripts/${encodeURIComponent(scriptFile)}`
    );
  }

  static async updateScene(
    projectName: string,
    sceneId: string,
    scriptFile: string,
    updates: Record<string, unknown>
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/scenes/${encodeURIComponent(sceneId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ script_file: scriptFile, updates }),
      }
    );
  }

  // ==================== 片段管理（说书模式） ====================

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

  // ==================== 文件管理 ====================

  static async uploadFile(
    projectName: string,
    uploadType: string,
    file: File,
    name: string | null = null
  ): Promise<{ success: boolean; path: string; url: string }> {
    const formData = new FormData();
    formData.append("file", file);

    let url = `/projects/${encodeURIComponent(projectName)}/upload/${uploadType}`;
    if (name) {
      url += `?name=${encodeURIComponent(name)}`;
    }

    const response = await fetch(`${API_BASE}${url}`, withAuth({
      method: "POST",
      body: formData,
    }));

    await throwIfNotOk(response, "上传失败");

    return response.json();
  }

  static async listFiles(
    projectName: string
  ): Promise<{ files: Record<string, { name: string; size: number; url: string }[]> }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/files`
    );
  }

  static getFileUrl(
    projectName: string,
    path: string,
    cacheBust?: number | string | null
  ): string {
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
    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`,
      withAuth()
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
    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`,
      withAuth({
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: content,
      })
    );
    await throwIfNotOk(response, "保存文件失败");
    return response.json();
  }

  /**
   * 删除 source 文件
   */
  static async deleteSourceFile(
    projectName: string,
    filename: string
  ): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectName)}/source/${encodeURIComponent(filename)}`,
      withAuth({
        method: "DELETE",
      })
    );
    await throwIfNotOk(response, "删除文件失败");
    return response.json();
  }

  // ==================== 草稿文件管理 ====================

  /**
   * 获取项目的所有草稿
   */
  static async listDrafts(
    projectName: string
  ): Promise<{ drafts: DraftInfo[] }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/drafts`
    );
  }

  /**
   * 获取草稿内容
   */
  static async getDraftContent(
    projectName: string,
    episode: number,
    stepNum: number
  ): Promise<string> {
    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectName)}/drafts/${episode}/step${stepNum}`,
      withAuth()
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
    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectName)}/drafts/${episode}/step${stepNum}`,
      withAuth({
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: content,
      })
    );
    await throwIfNotOk(response, "保存草稿失败");
    return response.json();
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
  ): Promise<{ success: boolean; task_id: string; message: string }> {
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
    durationSeconds: number = 4
  ): Promise<{ success: boolean; task_id: string; message: string }> {
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
   * 生成人物设计图
   * @param projectName - 项目名称
   * @param charName - 人物名称
   * @param prompt - 人物描述 prompt
   */
  static async generateCharacter(
    projectName: string,
    charName: string,
    prompt: string
  ): Promise<{
    success: boolean;
    task_id: string;
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
   * 生成线索设计图
   * @param projectName - 项目名称
   * @param clueName - 线索名称
   * @param prompt - 线索描述 prompt
   */
  static async generateClue(
    projectName: string,
    clueName: string,
    prompt: string
  ): Promise<{
    success: boolean;
    task_id: string;
    message: string;
  }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/generate/clue/${encodeURIComponent(clueName)}`,
      {
        method: "POST",
        body: JSON.stringify({ prompt }),
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
  ): Promise<TaskStats> {
    const params = new URLSearchParams();
    if (projectName) params.append("project_name", projectName);
    const query = params.toString();
    return this.request(`/tasks/stats${query ? "?" + query : ""}`);
  }

  static openTaskStream(options: TaskStreamOptions = {}): EventSource {
    const params = new URLSearchParams();
    if (options.projectName)
      params.append("project_name", options.projectName);
    const parsedLastEventId = Number(options.lastEventId);
    if (Number.isFinite(parsedLastEventId) && parsedLastEventId > 0) {
      params.append("last_event_id", String(parsedLastEventId));
    }

    const query = params.toString();
    const url = withAuthQuery(`${API_BASE}/tasks/stream${query ? "?" + query : ""}`);
    const source = new EventSource(url);

    const parsePayload = (event: MessageEvent): unknown | null => {
      try {
        return JSON.parse(event.data || "{}");
      } catch (err) {
        console.error("解析 SSE 数据失败:", err, event.data);
        return null;
      }
    };

    source.addEventListener("snapshot", (event) => {
      const payload = parsePayload(event as MessageEvent);
      if (payload && typeof options.onSnapshot === "function") {
        options.onSnapshot(
          payload as TaskStreamSnapshotPayload,
          event as MessageEvent
        );
      }
    });

    source.addEventListener("task", (event) => {
      const payload = parsePayload(event as MessageEvent);
      if (payload && typeof options.onTask === "function") {
        options.onTask(
          payload as TaskStreamTaskPayload,
          event as MessageEvent
        );
      }
    });

    source.onerror = (event: Event) => {
      if (typeof options.onError === "function") {
        options.onError(event);
      }
    };

    return source;
  }

  static openProjectEventStream(options: ProjectEventStreamOptions): EventSource {
    const url = withAuthQuery(
      `${API_BASE}/projects/${encodeURIComponent(options.projectName)}/events/stream`
    );
    const source = new EventSource(url);

    const parsePayload = (event: MessageEvent): unknown | null => {
      try {
        return JSON.parse(event.data || "{}");
      } catch (err) {
        console.error("解析项目事件 SSE 数据失败:", err, event.data);
        return null;
      }
    };

    const createHandler = (
      callback?: (payload: any, event: MessageEvent) => void
    ) => {
      return (event: Event) => {
        if (typeof callback !== "function") return;
        const payload = parsePayload(event as MessageEvent);
        if (payload) {
          callback(payload, event as MessageEvent);
        }
      };
    };

    source.addEventListener("snapshot", createHandler(options.onSnapshot));
    source.addEventListener("changes", createHandler(options.onChanges));

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
   * @param resourceType - 资源类型 (storyboards, videos, characters, clues)
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

    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectName)}/style-image`,
      withAuth({
        method: "POST",
        body: formData,
      })
    );

    await throwIfNotOk(response, "上传失败");

    return response.json();
  }

  /**
   * 删除风格参考图
   * @param projectName - 项目名称
   */
  static async deleteStyleImage(
    projectName: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/style-image`,
      {
        method: "DELETE",
      }
    );
  }

  /**
   * 更新风格描述
   * @param projectName - 项目名称
   * @param styleDescription - 风格描述
   */
  static async updateStyleDescription(
    projectName: string,
    styleDescription: string
  ): Promise<SuccessResponse> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/style-description`,
      {
        method: "PATCH",
        body: JSON.stringify({ style_description: styleDescription }),
      }
    );
  }

  // ==================== 助手会话 API ====================

  /** Build the project-scoped assistant base path. */
  private static assistantBase(projectName: string): string {
    return `/projects/${encodeURIComponent(projectName)}/assistant`;
  }

  static async createAssistantSession(
    projectName: string,
    title: string = ""
  ): Promise<{ success: boolean; session: SessionMeta }> {
    return this.request(`${this.assistantBase(projectName)}/sessions`, {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  }

  static async listAssistantSessions(
    projectName: string,
    status: string | null = null
  ): Promise<{ sessions: SessionMeta[] }> {
    const params = new URLSearchParams();
    if (status) params.append("status", status);
    const query = params.toString();
    return this.request(
      `${this.assistantBase(projectName)}/sessions${query ? "?" + query : ""}`
    );
  }

  static async getAssistantSession(
    projectName: string,
    sessionId: string
  ): Promise<{ session: SessionMeta }> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}`
    );
  }

  static async getAssistantSnapshot(
    projectName: string,
    sessionId: string
  ): Promise<AssistantSnapshot> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/snapshot`
    );
  }

  static async sendAssistantMessage(
    projectName: string,
    sessionId: string,
    content: string
  ): Promise<SuccessResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ content }),
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

  static getAssistantStreamUrl(projectName: string, sessionId: string): string {
    return withAuthQuery(`${API_BASE}${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}/stream`);
  }

  static async listAssistantSkills(
    projectName: string
  ): Promise<{ skills: SkillInfo[] }> {
    return this.request(
      `${this.assistantBase(projectName)}/skills`
    );
  }

  static async updateAssistantSession(
    projectName: string,
    sessionId: string,
    updates: Partial<Pick<SessionMeta, "title" | "status">>
  ): Promise<SuccessResponse> {
    return this.request(
      `${this.assistantBase(projectName)}/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(updates),
      }
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
    filters: UsageStatsFilters = {}
  ): Promise<Record<string, unknown>> {
    const params = new URLSearchParams();
    if (filters.projectName)
      params.append("project_name", filters.projectName);
    if (filters.startDate) params.append("start_date", filters.startDate);
    if (filters.endDate) params.append("end_date", filters.endDate);
    const query = params.toString();
    return this.request(`/usage/stats${query ? "?" + query : ""}`);
  }

  /**
   * 获取调用记录列表
   * @param filters - 筛选条件
   */
  static async getUsageCalls(
    filters: UsageCallsFilters = {}
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
    return this.request(`/usage/calls${query ? "?" + query : ""}`);
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
}

export { API };
