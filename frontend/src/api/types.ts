/**
 * API 共享类型：请求载荷、查询筛选与跨端点复用的响应结构。
 * 对外经 `@/api` 再导出，调用方继续从 `@/api` 导入。
 */

import type {
  EpisodeScript,
  ProjectChangeBatchPayload,
  ProjectDeletedPayload,
  ProjectEventSnapshotPayload,
} from "@/types";
import type { GenerationRoute } from "@/utils/generation-mode";
import type { SseStreamError } from "@/utils/sse-stream";

/** 项目内四类资产（与后端 ASSET_SPECS 的 asset_type 对齐）。 */
export type ProjectAssetType = "character" | "scene" | "prop" | "product";

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

export interface EpisodeScriptSnapshot {
  script: EpisodeScript;
  revision: string;
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

/** 分镜/视频单元媒体上传的统一响应。 */
export interface ShotUploadResult {
  success: boolean;
  path: string;
  version: number;
  asset_fingerprints: Record<string, number>;
}

export interface ProjectEventStreamOptions {
  projectName: string;
  /** 每次建连（含断线重建）后服务端都会先发一次 snapshot。 */
  onSnapshot?: (payload: ProjectEventSnapshotPayload) => void;
  onChanges?: (payload: ProjectChangeBatchPayload) => void;
  /** 项目目录被删除后收到一次，随后服务端正常关流；订阅方应在此关闭句柄以停止自动重建。 */
  onProjectDeleted?: (payload: ProjectDeletedPayload) => void;
  /** 连接失败或中断；`retryable` 为 true 时客户端随后自动重建。 */
  onError?: (error: SseStreamError) => void;
}

export interface AssistantEntriesStreamOptions {
  projectName: string;
  sessionId: string;
  /** 冷订阅游标（seq）；断线重建的续传由 `Last-Event-ID` 承担。 */
  after?: number;
  onEvent: (event: string, payload: Record<string, unknown>) => void;
  onError?: (error: SseStreamError) => void;
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

/** {@link API.getUsageRecords} 的筛选；数组维度在查询串里逗号分隔。 */
export interface UsageRecordsQuery {
  /** 空串筛选端点试跑记录，`undefined` 表示不按项目筛。 */
  projectName?: string;
  providers?: readonly string[];
  models?: readonly string[];
  mediaTypes?: readonly string[];
  statuses?: readonly string[];
  segmentIds?: readonly string[];
  /** ISO 8601 时刻，半开区间 [since, until)，作用于 started_at。 */
  since?: string;
  until?: string;
  limit?: number;
  /** 上一页返回的不透明游标。 */
  cursor?: string;
}

/** {@link API.getUsageSummary} 的筛选；不收状态，pending 不进聚合。 */
export interface UsageSummaryQuery {
  /** 空串筛选端点试跑记录，`undefined` 表示不按项目筛。 */
  projectName?: string;
  provider?: string;
  model?: string;
  mediaType?: string;
  since?: string;
  until?: string;
  /** IANA 时区名，按此切天；缺省 UTC。 */
  tz?: string;
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

/** Payload for {@link API.createProject}. */
export interface CreateProjectPayload {
  title: string;
  name?: string;
  content_mode?: "narration" | "drama" | "ad";
  /** 源文件性质：novel（默认）/ screenplay。仅 drama 暴露，创建即定、不可变。 */
  source_kind?: "novel" | "screenplay";
  aspect_ratio?: "9:16" | "16:9";
  /** 生成模式，创建时必填二选一、无默认值（后端缺失即 422）。 */
  generation_mode: GenerationRoute;
  /** 多宫格分镜装配开关，可随创建写入；仅分镜图生视频有意义。 */
  grid_storyboard?: boolean;
  /** 口播语速估算（阅读单位 / 秒）；留空即按项目语言的默认速度估算。 */
  speech_rate_units_per_second?: number | null;
  default_duration?: number | null;
  /** 单集目标时长（秒）；未设即不传。ad 项目服务端拒绝该字段。 */
  episode_target_duration?: number | null;
  /** 仅 ad：目标总时长（秒），UI 四档 15/30/60/90。 */
  target_duration?: number;
  /** 仅 ad：创作诉求短文本（可空）。 */
  brief?: string | null;
  style_template_id?: string | null;
  video_backend?: string | null;
  image_backend?: string | null;
  /** 项目默认图片模型。创建向导只暴露默认层（docs/adr/0054），任务类型桶留给项目设置页。 */
  default_image_backend?: string | null;
  text_backend_simple?: string | null;
  text_backend_complex?: string | null;
  default_text_backend?: string | null;
  model_settings?: Record<string, { resolution?: string | null }>;
}

export interface VideoCapabilitiesQuery {
  signal?: AbortSignal;
  videoBackend?: string;
  /** undefined = 服务端按项目已保存档位；null = 显式「自动」（发空串）；字符串 = 按该档位。 */
  resolution?: string | null;
  usesReferenceImages?: boolean;
}
