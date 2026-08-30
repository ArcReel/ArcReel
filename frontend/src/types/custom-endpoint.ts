// 自定义调用端点（custom endpoint）——声明式协议定义的前端类型。
// 定义 JSON 本身是唯一真相源，导入导出零封套：文件即 definition 原样 JSON。
// 后端 schema 在 lib/custom_provider/endpoint_definition/schema.json，最终判定以
// POST /custom-endpoints/validate 为准，这里只描述 UI 需要读写的形状。

/** 素材在 ArcReel 侧的来源槽位。 */
export type EndpointInputSource =
  | "start_image"
  | "end_image"
  | "reference_images"
  | "reference_audio_files";

/** 素材发送到供应商时的编码形态。 */
export type EndpointInputEncoding = "data_uri" | "base64";

/** 标准状态四档，与后端 ProviderJobStatus 同名。 */
export type EndpointStandardStatus = "queued" | "running" | "succeeded" | "failed";

export interface EndpointInputSpec {
  source: EndpointInputSource;
  encoding: EndpointInputEncoding;
  required?: boolean;
}

/** 取值路径项：JSONPath 串，或带 json_decode 的 JSON-in-string 后缀形态。 */
export type EndpointPathItem =
  | string
  | { path: string; json_decode?: boolean; then?: string[] };

/** 取值声明：简写为路径优先级数组，全写可另带 accept。 */
export type EndpointExtractSpec =
  | EndpointPathItem[]
  | { paths: EndpointPathItem[]; accept?: "string" | "scalar" };

export interface EndpointMetaHints {
  base_url?: string;
  suggested_models?: { id: string; label?: string }[];
}

export interface EndpointMeta {
  name: string;
  author: string;
  version: string;
  description?: string;
  homepage?: string;
  hints?: EndpointMetaHints;
}

export interface EndpointAuth {
  headers?: Record<string, string>;
  query?: Record<string, string>;
}

export interface EndpointSubmitSpec {
  method: string;
  url: string;
  headers?: Record<string, string>;
  body?: unknown;
  extract: {
    task_id?: EndpointExtractSpec;
    error?: EndpointExtractSpec;
  };
}

export interface EndpointPollSpec {
  method: string;
  url: string;
  headers?: Record<string, string>;
  body?: unknown;
  expire_on_404?: boolean;
  extract: {
    status?: EndpointExtractSpec;
    video_url?: EndpointExtractSpec;
    error?: EndpointExtractSpec;
    failure?: EndpointExtractSpec;
    result_id?: EndpointExtractSpec;
    usage?: Record<string, EndpointExtractSpec>;
  };
}

export interface EndpointResultSpec {
  method: string;
  url: string;
  headers?: Record<string, string>;
  extract: {
    video_url?: EndpointExtractSpec;
    error?: EndpointExtractSpec;
    usage?: Record<string, EndpointExtractSpec>;
  };
}

export interface EndpointCapabilities {
  text_to_video?: boolean;
  first_frame?: boolean;
  last_frame?: boolean;
  max_reference_images?: number;
  reference_audio_mode?: "none" | "direct";
  max_reference_audio_count?: number;
  max_reference_audio_total_seconds?: number | null;
  reference_audio_per_image?: boolean;
  max_prompt_chars?: number | null;
  first_frame_ratio_adaptive_only?: boolean;
  audio_track?: "controllable" | "always_on" | "always_off";
  reference_route_audio_track?: "controllable" | "always_on" | "always_off" | null;
}

/** 一份完整的声明式定义。未知字段原样保留，表单不会在往返中丢弃它们。 */
export interface EndpointDefinition {
  kind: "declarative";
  schema_version: string;
  meta: EndpointMeta;
  auth: EndpointAuth;
  inputs?: Record<string, EndpointInputSpec>;
  enum_maps?: Record<string, Record<string, string | number | boolean>>;
  defaults?: Record<string, string | number | boolean>;
  submit: EndpointSubmitSpec;
  poll: EndpointPollSpec;
  result?: EndpointResultSpec;
  status_map?: Record<string, EndpointStandardStatus>;
  capabilities?: EndpointCapabilities;
}

// ---------------------------------------------------------------------------
// CRUD / validate
// ---------------------------------------------------------------------------

export interface CustomEndpointInfo {
  id: number;
  /** 系统分配的 `ce-<id>`；对用户无意义，界面不展示。 */
  key: string;
  display_name: string;
  kind: string;
  schema_version: string;
  media_type: string;
  definition: EndpointDefinition;
  created_at: string | null;
  updated_at: string | null;
}

/** 删除端点被拒时，服务端返回的实际引用模型行。 */
export interface EndpointReference {
  provider_id: number;
  provider_display_name: string;
  model_id: string;
  model_display_name: string;
}

export interface EndpointDefinitionIssue {
  /** 定义 JSON 内的定位串，根为 `$`，如 `poll.extract.video_url[0]`。 */
  path: string;
  code: string;
  /** 服务端已按 Accept-Language 渲染好的说明，前端直接展示。 */
  message: string;
}

/** 导入时按 meta.author + meta.name 判定的同血统既有定义。 */
export interface EndpointDuplicateDescriptor {
  id: number;
  key: string;
  display_name: string;
  version: string;
  relation: "newer" | "same" | "older";
}

export interface EndpointSchemaVersionInfo {
  file: string | null;
  current: string;
  level: "direct" | "warning" | "confirm";
}

export interface EndpointValidateResponse {
  errors: EndpointDefinitionIssue[];
  warnings: EndpointDefinitionIssue[];
  duplicates: EndpointDuplicateDescriptor[];
  hints: EndpointMetaHints | null;
  schema_version: EndpointSchemaVersionInfo;
}

// ---------------------------------------------------------------------------
// 端点测试三模式
// ---------------------------------------------------------------------------

export interface EndpointTestParameters {
  model: string;
  prompt?: string;
  duration_seconds?: number;
  aspect_ratio?: string;
  resolution?: string | null;
  generate_audio?: boolean;
}

/** 凭证来源二选一：已保存的自定义供应商，或本次测试临时输入。 */
export interface EndpointTestCredentials {
  provider_id?: string;
  base_url?: string;
  api_key?: string;
}

/** 端点测试 multipart 素材；键与服务端表单字段同名。 */
export type EndpointTestAssets = Partial<Record<EndpointInputSource, File[]>>;

export interface PreviewedRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface EndpointPreviewResponse {
  submit: PreviewedRequest;
  poll: PreviewedRequest;
  result: PreviewedRequest | null;
}

export type EndpointTestStage = "submit" | "poll" | "result";

export interface EndpointExtractionAttempt {
  path: string;
  json_decode: boolean;
  matched: boolean;
  value: unknown;
}

export interface EndpointExtractionField {
  key: string;
  value: unknown;
  attempts: EndpointExtractionAttempt[];
}

/** 单个阶段的取值报告，check-response 的响应与试跑结果的 extractions 同形。 */
export interface EndpointStageReport {
  stage: EndpointTestStage;
  fields: EndpointExtractionField[];
  task_id?: string | null;
  raw_status?: unknown;
  status?: EndpointStandardStatus | null;
  video_url?: string | null;
  error?: string | null;
  result_id?: string | null;
  duration_seconds?: number | null;
}

export type TrialRunStatus = "queued" | "running" | "succeeded" | "failed";

export interface TrialRunInfo {
  id: string;
  status: TrialRunStatus;
  provider: string;
  model: string;
  /** epoch 秒。 */
  created_at: number;
  finished_at: number | null;
  api_call_id: number | null;
  request: PreviewedRequest | null;
  submit_response: unknown;
  poll_responses: unknown[];
  extractions: Partial<Record<EndpointTestStage, EndpointStageReport>>;
  video_url: string | null;
  duration_seconds: number | null;
  error: string | null;
  has_artifact: boolean;
}

export interface TrialRunModelRef {
  provider_id: string;
  model_id: string;
}
