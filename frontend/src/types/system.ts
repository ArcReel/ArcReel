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
  market_github_proxy_prefix?: string;
}

export interface SystemConfigOptions {
  video_backends: string[];
  image_backends: string[];
  text_backends: string[];
  audio_backends?: string[];
  provider_names?: Record<string, string>;
  model_names?: Record<string, string>;
}

export interface NarrationDefaultsResponse {
  audio_backend: string | null;
  narration_voice: string;
  narration_speed: number | null;
}

export interface TtsVoiceOption {
  id: string;
  label: string;
}

export interface TtsModelCapabilitiesResponse {
  supports_speed: boolean;
  voices: TtsVoiceOption[];
}

export interface GetSystemConfigResponse {
  settings: SystemConfigSettings;
  options: SystemConfigOptions;
}

export type GenerationTypeBucket = "t2i" | "i2i" | "i2v" | "r2v";

export type AutoBackendsResponse = Record<GenerationTypeBucket | "text", string | null> & { audio?: string | null };

export interface RecommendationChange {
  media_type: "text" | "image" | "video" | "audio";
  provider_id: string;
  recommended: string;
  current: string | null;
}

export interface RecommendationChangesResponse {
  changes: RecommendationChange[];
}

export type BucketDimension = "text_to_image" | "image_to_image" | "max_reference_images" | "first_frame";

export interface FilteredCandidate {
  option: string;
  unmet: BucketDimension[];
}

export interface MediaCandidates {
  default: string[];
  buckets: Partial<Record<GenerationTypeBucket, string[]>>;
  filtered: Partial<Record<GenerationTypeBucket, FilteredCandidate[]>>;
}

export type ModelReferenceLayer = "default" | GenerationTypeBucket;

export interface ModelLifecycleNotice {
  status: "retiring" | "discontinued" | "unavailable";
  shutdown_date: string | null;
  successor: string | null;
  successor_name: string | null;
  switch_targets: Partial<Record<ModelReferenceLayer, string | null>>;
}

export interface ModelCandidatesResponse {
  image: MediaCandidates;
  video: MediaCandidates;
  provider_names: Record<string, string>;
  model_names: Record<string, string>;
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
