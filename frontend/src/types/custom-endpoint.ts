import type { EndpointInstallation } from "./market";
// Custom endpoints: frontend endpoint definition types, divided by kind into declarative and ComfyUI.
// The definition JSON is the sole source of truth, imported/exported without an envelope: the file is the unchanged definition JSON.
// Backend schemas are endpoint_definition/schema.json and comfyui/schema.json under
// packages/arcreel-market-core/src/arcreel_market_core/; POST /custom-endpoints/validate is authoritative,
// and these types describe only shapes the UI needs to read and write.

/** Source slot for media on the ArcReel side. */
export type EndpointInputSource =
  | "start_image"
  | "end_image"
  | "reference_images"
  | "reference_audio_files";

/** Encoding used when sending media to the provider. */
export type EndpointInputEncoding = "data_uri" | "base64";

/** The four standard states, matching backend ProviderJobStatus. */
export type EndpointStandardStatus = "queued" | "running" | "succeeded" | "failed";

export interface EndpointInputSpec {
  source: EndpointInputSource;
  encoding: EndpointInputEncoding;
  required?: boolean;
}

/** Extraction path item: a JSONPath string, or a JSON-in-string suffix form with json_decode. */
export type EndpointPathItem =
  | string
  | { path: string; json_decode?: boolean; then?: string[] };

/** Extraction declaration: shorthand is an array of paths in priority order; the full form may also include accept. */
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
  /** Minimum ArcReel version required to run this definition (semver). */
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

/**
 * Capability fields are grouped by media type: video definitions use fields matching VideoCapabilities,
 * while image definitions use text_to_image, image_to_image, and max_reference_images.
 */
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

/** A complete declarative definition. Unknown fields are preserved unchanged across form round trips. */
export interface EndpointDefinition {
  kind: "declarative";
  schema_version: string;
  /** Media type produced by the definition; defaults to video when omitted. */
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

/**
 * Semantic key list for node bindings. Video endpoints use all eleven; image endpoints omit the four for first/last frames and timing.
 * Matches the names and order in server-side `packages/arcreel-market-core/src/arcreel_market_core/comfyui/bindings.py`.
 */
export const COMFYUI_VIDEO_BINDING_KEYS = [
  "prompt",
  "negative_prompt",
  "start_image",
  "end_image",
  "reference_images",
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
  "width",
  "height",
  "seed",
  "output",
];

/** Semantic keys required for both media types: a prompt is needed to generate, and an output is needed to retrieve the result. */
export const COMFYUI_REQUIRED_BINDING_KEYS: readonly ComfyuiBindingKey[] = ["prompt", "output"];

/** Whether a ComfyUI workflow produces images or video. */
export type ComfyuiMediaType = "image" | "video";

/** The node and input consuming the bound image-loading node; used to rewrite the graph when image count is below slot count. */
export interface ComfyuiConsumerTarget {
  node: string;
  input: string;
  class_type: string;
  title?: string;
}

/**
 * Target of a node binding. The server schema constrains optional fields per semantic key: `output` has no `input`,
 * `fps` always has `direction: read`; `width` / `height` / `frames` carry `step`,
 * `frames` may carry a manually entered `fps`, `seed` carries `policy`, and `reference_images` carries `consumer`.
 */
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
}

/**
 * Semantic key → target list. Three states: a nonempty list means bound, an empty list means explicitly unsupported, and an absent key means never inferred.
 */
export type ComfyuiBindings = Partial<Record<ComfyuiBindingKey, ComfyuiBindingTarget[]>>;

/**
 * A ComfyUI endpoint definition: an API-format workflow together with its node bindings.
 *
 * Capabilities derive only from `bindings`; the definition has no `capabilities` section. `workflow` remains loosely typed: ArcReel does not rewrite
 * its structure and submits it unchanged; the frontend reads only each node's `class_type`, `inputs`, and `_meta.title`.
 */
export interface ComfyuiEndpointDefinition {
  kind: "comfyui";
  schema_version: string;
  meta: EndpointMeta;
  media_type: ComfyuiMediaType;
  auth?: EndpointAuth;
  workflow: Record<string, unknown>;
  bindings: ComfyuiBindings;
}

/** One workflow node. `inputs` values are either literals or connections shaped as `[node id, output index]`. */
export interface ComfyuiWorkflowNode {
  class_type: string;
  inputs?: Record<string, unknown>;
  _meta?: { title?: string };
}

// ---------------------------------------------------------------------------
// Node binding inference (POST /custom-endpoints/comfyui/infer)
// ---------------------------------------------------------------------------

/**
 * Inference state for a semantic key. `needs_confirmation` applies only to reimport: existing entries were lost, this key was
 * reinferred, and the user must confirm before saving.
 */
export type ComfyuiBindingState =
  | "auto_selected"
  | "ambiguous"
  | "not_found"
  | "unsupported"
  | "needs_confirmation";

/** Candidate origin: inferred this round, kept unchanged from saved entries, or rematched to a new node ID by type and title. */
export type ComfyuiMatchOrigin = "inferred" | "kept" | "rematched";

/** One matched signal and its weight contribution; `message` is rendered in the request language. */
export interface ComfyuiBindingSignal {
  signal: string;
  weight: number;
  message: string;
}

/** An informational note. Does not block saving, but explains how a particular configuration will behave. */
export interface ComfyuiInferenceNote {
  code: string;
  message: string;
}

export interface ComfyuiBindingCandidate {
  /** Entry ready to write into `bindings`, with `step` / `policy` / `direction` / `consumer` already populated. */
  target: ComfyuiBindingTarget;
  score: number;
  signals: ComfyuiBindingSignal[];
  selected: boolean;
  origin: ComfyuiMatchOrigin;
  /** Upstream chain length of an output candidate, explaining why it was selected; null for other semantic keys. */
  depth: number | null;
}

export interface ComfyuiKeyInference {
  state: ComfyuiBindingState;
  candidates: ComfyuiBindingCandidate[];
  notes: ComfyuiInferenceNote[];
}

export interface ComfyuiInferResponse {
  media_type: ComfyuiMediaType;
  /** Whether persisting this result directly would pass validation; the actual gate remains the save-time validator. */
  savable: boolean;
  bindings: Partial<Record<ComfyuiBindingKey, ComfyuiKeyInference>>;
  notes: ComfyuiInferenceNote[];
  import_shape: EndpointImportShape;
  /** Wrapped raw API workflow; null when the payload is already a definition. */
  wrapped_definition: ComfyuiEndpointDefinition | null;
}

/** Definition flowing through import, validation, and save: either kind is possible. */
export type AnyEndpointDefinition = EndpointDefinition | ComfyuiEndpointDefinition;

// ---------------------------------------------------------------------------
// CRUD / validate
// ---------------------------------------------------------------------------

export interface CustomEndpointInfo {
  installation: EndpointInstallation | null;
  id: number;
  /** System-assigned `ce-<id>`; meaningless to users and not displayed. */
  key: string;
  display_name: string;
  kind: string;
  schema_version: string;
  media_type: string;
  /** Either kind is possible: ComfyUI definitions have no `submit` / `poll`; narrow with `isDeclarativeDefinition` before reading them. */
  definition: AnyEndpointDefinition;
  created_at: string | null;
  updated_at: string | null;
}

/** Actual referencing model rows returned by the server when endpoint deletion is refused. */
export interface EndpointReference {
  provider_id: number;
  provider_display_name: string;
  model_id: string;
  model_display_name: string;
}

export interface EndpointDefinitionIssue {
  /** Location within definition JSON, rooted at `$`, such as `poll.extract.video_url[0]`. */
  path: string;
  code: string;
  /** Explanation rendered by the server according to Accept-Language, displayed directly by the frontend. */
  message: string;
}

/** Existing definition with the same author and name, identified during import by meta.author + meta.name. */
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

/** Comparison of meta.min_app_version with the current app version; unmet requirements produce a notice without blocking import. */
export interface EndpointMinAppVersionInfo {
  required: string;
  current: string;
  satisfied: boolean;
}

/**
 * How the server classified the received payload.
 *
 * Users most commonly have a workflow exported by ComfyUI rather than an endpoint definition, and the export menu has two options:
 * `Export (API)` can be submitted; the canvas archive from `Export` cannot.
 */
export type EndpointImportShape = "endpoint_definition" | "comfyui_api_workflow" | "comfyui_ui_workflow";

export interface EndpointValidateResponse {
  errors: EndpointDefinitionIssue[];
  warnings: EndpointDefinitionIssue[];
  duplicates: EndpointDuplicateDescriptor[];
  hints: EndpointMetaHints | null;
  schema_version: EndpointSchemaVersionInfo;
  /** Null when the definition declares no minimum or the app version cannot be read. */
  min_app_version: EndpointMinAppVersionInfo | null;
  import_shape: EndpointImportShape;
  /** Wrapped raw API workflow; null for the other two shapes, for which the client continues using its own copy. */
  wrapped_definition: ComfyuiEndpointDefinition | null;
}

// ---------------------------------------------------------------------------
// Three endpoint test modes
// ---------------------------------------------------------------------------

export interface EndpointTestParameters {
  model: string;
  prompt?: string;
  duration_seconds?: number;
  aspect_ratio?: string;
  resolution?: string | null;
  generate_audio?: boolean;
}

/** Mutually exclusive credential sources: a saved custom provider, or temporary input for this test. */
export interface EndpointTestCredentials {
  provider_id?: string;
  base_url?: string;
  api_key?: string;
}

/** Multipart media for endpoint testing; keys match the server's form fields. */
export type EndpointTestAssets = Partial<Record<EndpointInputSource, File[]>>;

export interface PreviewedRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

/**
 * How this request was calculated. ComfyUI endpoint dimensions, frame counts, seeds, and graph rewrites are calculated; inspecting a workflow JSON with dozens of
 * nodes cannot explain exactly how many pixels the selected 720p became.
 *
 * Null `width` / `height` / `frames` / `seed` means that dimension did not drive this workflow (unbound, or only one size dimension
 * was bound and size was therefore considered fixed); literal workflow values remain unchanged.
 */
export interface ComfyuiRequestConversions {
  /** Fingerprint of the workflow actually sent, matching the value recorded in output version metadata. */
  workflow_sha256: string;
  aspect_ratio: string;
  resolution: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  frames: number | null;
  seed: number | null;
  negative_prompt: string;
  /** Nodes removed by the graph rewrite for this reference image count. */
  dropped_nodes: string[];
}

export interface EndpointPreviewResponse {
  submit: PreviewedRequest;
  poll: PreviewedRequest;
  result: PreviewedRequest | null;
  /** Null for declarative endpoints: all requests come directly from template substitution, with no conversions to explain. */
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

/** Extraction report for one stage; check-response responses and trial result extractions share this shape. */
export interface EndpointStageReport {
  stage: EndpointTestStage;
  fields: EndpointExtractionField[];
  task_id?: string | null;
  raw_status?: unknown;
  status?: EndpointStandardStatus | null;
  video_url?: string | null;
  image_url?: string | null;
  /** Decoded image byte count extracted from `image_b64`; null when no image can be decoded. */
  image_bytes?: number | null;
  error?: string | null;
  result_id?: string | null;
  duration_seconds?: number | null;
}

export type TrialRunStatus = "queued" | "running" | "succeeded" | "failed";

/** Four stages of a connection test: submit, poll, obtain result, retrieve artifact. */
export const TRIAL_RUN_STAGES = ["submit", "poll", "result", "artifact"] as const;

export type TrialRunStage = (typeof TRIAL_RUN_STAGES)[number];

/** Reached stages are done; unreached stages are skipped in terminal states or pending while running. Does not mark which stage failed. */
export type TrialRunStageState = "done" | "pending" | "skipped";

export interface TrialRunInfo {
  id: string;
  status: TrialRunStatus;
  provider: string;
  model: string;
  /** Whether this run produces video or an image; determines how its artifact is displayed. */
  media_type: ComfyuiMediaType;
  /** Epoch seconds. */
  created_at: number;
  finished_at: number | null;
  api_call_id: number | null;
  /** Provider-assigned ID for this run (`prompt_id` in ComfyUI); null before submission or when unrecognized. */
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
  /** Stable failure code behind `error`; null for raw exception text without a code. */
  error_code: string | null;
  /** What this failure code asks the user to do, using the same `GenerationAction` values as generation failures on project pages. */
  error_action: string | null;
  has_artifact: boolean;
}

export interface TrialRunModelRef {
  provider_id: string;
  model_id: string;
}
