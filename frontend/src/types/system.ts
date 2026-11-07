export interface SystemConfigSettings {
  default_video_backend: string;
  default_video_backend_i2v?: string;
  default_video_backend_r2v?: string;
  default_image_backend: string;
  default_image_backend_t2i?: string;
  default_image_backend_i2i?: string;
  default_text_backend: string;
  default_audio_backend?: string;
  narration_voice?: string;
  narration_speed?: number | null;
  text_backend_simple: string;
  text_backend_complex: string;
  video_generate_audio: boolean;
  video_poll_timeout_seconds: number;
  anthropic_api_key: { is_set: boolean; masked: string | null };
  anthropic_base_url: string;
  anthropic_model: string;
  anthropic_default_haiku_model: string;
  anthropic_default_opus_model: string;
  anthropic_default_sonnet_model: string;
  claude_code_subagent_model: string;
  agent_session_cleanup_delay_seconds: number;
  agent_max_concurrent_sessions: number;
  /** GitHub raw proxy prefix for market sources; an empty string means a direct connection. */
  market_github_proxy_prefix?: string;
}

export interface SystemConfigOptions {
  video_backends: string[];
  image_backends: string[];
  text_backends: string[];
  audio_backends?: string[];
  provider_names?: Record<string, string>;
  /** "provider_id/model_id" → model name localized according to Accept-Language; missing keys fall back to the model ID. */
  model_names?: Record<string, string>;
}

/** Prefilled values for a new TTS project; audio_backend is null when no audio provider is configured. */
export interface NarrationDefaultsResponse {
  audio_backend: string | null;
  narration_voice: string;
  narration_speed: number | null;
}

/** TTS voice catalog entry: id is the voice value in synthesis requests, and label is the display name rendered in the UI language. */
export interface TtsVoiceOption {
  id: string;
  label: string;
}

export interface TtsModelCapabilitiesResponse {
  supports_speed: boolean;
  /** Voice list for the model's endpoint; different endpoints from the same provider have different voices. */
  voices: TtsVoiceOption[];
}

export interface GetSystemConfigResponse {
  settings: SystemConfigSettings;
  options: SystemConfigOptions;
}

/** Task type bucket key (docs/adr/0054). An internal code term not shown directly in UI copy. */
export type GenerationTypeBucket = "t2i" | "i2i" | "i2v" | "r2v";

/**
 * Models (provider/model) automatically selected by the backend when all project and global layers are unset for each purpose, matching execution-time resolution;
 * null when no provider is available. Text tiers share the same automatic inference and are combined into one entry.
 */
export type AutoBackendsResponse = Record<GenerationTypeBucket | "text", string | null>;

/** Unmet capability dimensions when a task type bucket filters out a model; keys match the capabilities API. */
export type BucketDimension = "text_to_image" | "image_to_image" | "max_reference_images" | "first_frame";

/** A model present in the default layer but excluded from a task type bucket for unmet capabilities. */
export interface FilteredCandidate {
  option: string;
  /** Unmet capability dimensions; empty when indeterminate (the endpoint is discontinued or the backend declares no capabilities). */
  unmet: BucketDimension[];
}

/** Candidates for one media_type: the full default layer, capability-filtered subsets for each task type bucket, and the models excluded from each bucket with reasons. */
export interface MediaCandidates {
  default: string[];
  buckets: Partial<Record<GenerationTypeBucket, string[]>>;
  /** Disabled or discontinued models, and those requiring a newer minimum app version, are absent from both the default layer and this mapping. */
  filtered: Partial<Record<GenerationTypeBucket, FilteredCandidate[]>>;
}

/** Reference layer: `default` is the default layer (also used by text tiers and narration models); other values are task type buckets for that media type. */
export type ModelReferenceLayer = "default" | GenerationTypeBucket;

/**
 * Lifecycle notice at a reference: retiring or discontinued provider models, with the official successor and one-click switch target for each reference layer.
 * `unavailable`: the referenced model is absent from the provider listing, with no local discontinued entry; only a request to select again is shown.
 */
export interface ModelLifecycleNotice {
  status: "retiring" | "discontinued" | "unavailable";
  /** ISO date (YYYY-MM-DD); null when discontinued without a recorded date. */
  shutdown_date: string | null;
  /** The official successor's "model maker/official model name" and entry name; null when no successor is recorded. */
  successor: string | null;
  successor_name: string | null;
  /**
   * Reference layer → one-click switch target (provider/model): a provider model from the same provider, matching the successor and eligible at that layer.
   * Null when no such model exists, showing only the successor name. Keys include only layers that exist for that media type.
   */
  switch_targets: Partial<Record<ModelReferenceLayer, string | null>>;
}

export interface ModelCandidatesResponse {
  image: MediaCandidates;
  video: MediaCandidates;
  /** Provider ID → name localized according to Accept-Language, using the same rules for built-in and custom providers. */
  provider_names: Record<string, string>;
  /** "provider_id/model_id" → model name localized according to Accept-Language, covering candidates of all media types. */
  model_names: Record<string, string>;
  /**
   * "provider_id/model_id" → lifecycle notice; includes retiring and discontinued models (including non-candidates), plus models referenced in settings and projects
   * that are outside provider listings, covering all media types.
   */
  lifecycle: Record<string, ModelLifecycleNotice>;
}

export interface SystemVersionReleaseInfo {
  version: string;
  tag_name: string;
  name: string;
  body: string;
  html_url: string;
  published_at: string;
}

export interface GetSystemVersionResponse {
  current: { version: string };
  latest: SystemVersionReleaseInfo | null;
  has_update: boolean;
  checked_at: string;
  update_check_error: string | null;
}

/** Instance-wide "seen" state for onboarding; unset means unseen. */
export interface OnboardingStatus {
  seen: boolean;
}

export interface SystemConfigPatch {
  default_video_backend?: string;
  default_video_backend_i2v?: string;
  default_video_backend_r2v?: string;
  default_image_backend?: string;
  default_image_backend_t2i?: string;
  default_image_backend_i2i?: string;
  default_text_backend?: string;
  default_audio_backend?: string;
  narration_voice?: string;
  narration_speed?: number | null;
  text_backend_simple?: string;
  text_backend_complex?: string;
  video_generate_audio?: boolean;
  video_poll_timeout_seconds?: number;
  anthropic_api_key?: string;
  anthropic_base_url?: string;
  anthropic_model?: string;
  anthropic_default_haiku_model?: string;
  anthropic_default_opus_model?: string;
  anthropic_default_sonnet_model?: string;
  claude_code_subagent_model?: string;
  agent_session_cleanup_delay_seconds?: number;
  agent_max_concurrent_sessions?: number;
  market_github_proxy_prefix?: string;
}
