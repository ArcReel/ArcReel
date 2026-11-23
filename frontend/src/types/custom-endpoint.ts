import type { EndpointInstallation } from "./market";

export type EndpointInputSource =
  | "start_image"
  | "end_image"
  | "reference_images"
  | "reference_audio_files";

export type EndpointInputEncoding = "data_uri" | "base64";

export type EndpointStandardStatus = "queued" | "running" | "succeeded" | "failed";

export interface EndpointInputSpec {
  source: EndpointInputSource;
  encoding: EndpointInputEncoding;
  required?: boolean;
}

export type EndpointPathItem =
  | string
  | { path: string; json_decode?: boolean; then?: string[] };

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
  min_app_version?: string;
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
    image_url?: EndpointExtractSpec;
    image_b64?: EndpointExtractSpec;
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
    image_url?: EndpointExtractSpec;
    image_b64?: EndpointExtractSpec;
    error?: EndpointExtractSpec;
    usage?: Record<string, EndpointExtractSpec>;
  };
}

export interface EndpointCapabilities {
  text_to_video?: boolean;
  text_to_image?: boolean;
  image_to_image?: boolean;
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

export interface EndpointDefinition {
  kind: "declarative";
  schema_version: string;
  media_type?: "image" | "video";
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

export const COMFYUI_VIDEO_BINDING_KEYS = [
  "prompt",
  "negative_prompt",
  "start_image",
  "end_image",
  "reference_audio",
  "reference_images",
  "aspect_ratio",
  "width",
  "height",
  "frames",
  "fps",
  "seed",
  "output",
] as const;

export type ComfyuiBindingKey = (typeof COMFYUI_VIDEO_BINDING_KEYS)[number];

export const COMFYUI_IMAGE_BINDING_KEYS: readonly ComfyuiBindingKey[] = [
  "prompt",
  "negative_prompt",
  "reference_images",
  "aspect_ratio",
  "width",
  "height",
  "seed",
  "output",
];

export const COMFYUI_REQUIRED_BINDING_KEYS: readonly ComfyuiBindingKey[] = ["prompt", "output"];

export type ComfyuiMediaType = "image" | "video";

export interface ComfyuiConsumerTarget {
  node: string;
  input: string;
  class_type: string;
  title?: string;
}

export interface ComfyuiBindingTarget {
  node: string;
  input?: string;
  class_type: string;
  title?: string;
  direction?: "write" | "read";
  step?: number;
  fps?: number;
  policy?: "random" | "keep";
  consumer?: ComfyuiConsumerTarget;
  values?: Record<string, string>;
}

export type ComfyuiBindings = Partial<Record<ComfyuiBindingKey, ComfyuiBindingTarget[]>>;

export interface ComfyuiEndpointDefinition {
  kind: "comfyui";
  schema_version: string;
  meta: EndpointMeta;
  media_type: ComfyuiMediaType;
  auth?: EndpointAuth;
  workflow: Record<string, unknown>;
  bindings: ComfyuiBindings;
}

export interface ComfyuiWorkflowNode {
  class_type: string;
  inputs?: Record<string, unknown>;
  _meta?: { title?: string };
}


export type ComfyuiBindingState =
  | "auto_selected"
  | "ambiguous"
  | "not_found"
  | "unsupported"
  | "needs_confirmation";

export type ComfyuiMatchOrigin = "inferred" | "kept" | "rematched";

export interface ComfyuiBindingSignal {
  signal: string;
  weight: number;
  message: string;
}

export interface ComfyuiInferenceNote {
  code: string;
  message: string;
}

export interface ComfyuiBindingCandidate {
  target: ComfyuiBindingTarget;
  score: number;
  signals: ComfyuiBindingSignal[];
  selected: boolean;
  origin: ComfyuiMatchOrigin;
  depth: number | null;
}

export interface ComfyuiKeyInference {
  state: ComfyuiBindingState;
  candidates: ComfyuiBindingCandidate[];
  notes: ComfyuiInferenceNote[];
}

export interface ComfyuiInferResponse {
  media_type: ComfyuiMediaType;
  savable: boolean;
  bindings: Partial<Record<ComfyuiBindingKey, ComfyuiKeyInference>>;
  notes: ComfyuiInferenceNote[];
  import_shape: EndpointImportShape;
  wrapped_definition: ComfyuiEndpointDefinition | null;
}

export type AnyEndpointDefinition = EndpointDefinition | ComfyuiEndpointDefinition;


export interface CustomEndpointInfo {
  installation: EndpointInstallation | null;
  id: number;
  key: string;
  display_name: string;
  kind: string;
  schema_version: string;
  media_type: string;
  definition: AnyEndpointDefinition;
  created_at: string | null;
  updated_at: string | null;
}

export interface EndpointReference {
  provider_id: number;
  provider_display_name: string;
  model_id: string;
  model_display_name: string;
}

export interface EndpointDefinitionIssue {
  path: string;
  code: string;
  message: string;
}

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

export interface EndpointMinAppVersionInfo {
  required: string;
  current: string;
  satisfied: boolean;
}

export type EndpointImportShape = "endpoint_definition" | "comfyui_api_workflow" | "comfyui_ui_workflow";

export interface EndpointValidateResponse {
  errors: EndpointDefinitionIssue[];
  warnings: EndpointDefinitionIssue[];
  duplicates: EndpointDuplicateDescriptor[];
  hints: EndpointMetaHints | null;
  schema_version: EndpointSchemaVersionInfo;
  min_app_version: EndpointMinAppVersionInfo | null;
  import_shape: EndpointImportShape;
  wrapped_definition: ComfyuiEndpointDefinition | null;
}


export interface EndpointTestParameters {
  model: string;
  prompt?: string;
  duration_seconds?: number;
  aspect_ratio?: string;
  resolution?: string | null;
  generate_audio?: boolean;
}

export interface EndpointTestCredentials {
  provider_id?: string;
  base_url?: string;
  api_key?: string;
}

export type EndpointTestAssets = Partial<Record<EndpointInputSource, File[]>>;

export interface PreviewedRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface ComfyuiRequestConversions {
  workflow_sha256: string;
  aspect_ratio: string;
  resolution: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  frames: number | null;
  seed: number | null;
  negative_prompt: string;
  dropped_nodes: string[];
}

export interface EndpointPreviewResponse {
  submit: PreviewedRequest;
  poll: PreviewedRequest;
  result: PreviewedRequest | null;
  conversions: ComfyuiRequestConversions | null;
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

export interface EndpointStageReport {
  stage: EndpointTestStage;
  fields: EndpointExtractionField[];
  task_id?: string | null;
  raw_status?: unknown;
  status?: EndpointStandardStatus | null;
  video_url?: string | null;
  image_url?: string | null;
  image_bytes?: number | null;
  error?: string | null;
  result_id?: string | null;
  duration_seconds?: number | null;
}

export type TrialRunStatus = "queued" | "running" | "succeeded" | "failed";

export const TRIAL_RUN_STAGES = ["submit", "poll", "result", "artifact"] as const;

export type TrialRunStage = (typeof TRIAL_RUN_STAGES)[number];

export type TrialRunStageState = "done" | "pending" | "skipped";

export interface TrialRunInfo {
  id: string;
  status: TrialRunStatus;
  provider: string;
  model: string;
  media_type: ComfyuiMediaType;
  created_at: number;
  finished_at: number | null;
  api_call_id: number | null;
  provider_job_id: string | null;
  stages: Partial<Record<TrialRunStage, TrialRunStageState>>;
  request: PreviewedRequest | null;
  submit_response: unknown;
  poll_responses: unknown[];
  result_response: unknown;
  extractions: Partial<Record<EndpointTestStage, EndpointStageReport>>;
  video_url: string | null;
  duration_seconds: number | null;
  error: string | null;
  error_code: string | null;
  error_action: string | null;
  has_artifact: boolean;
}

export interface TrialRunModelRef {
  provider_id: string;
  model_id: string;
}
