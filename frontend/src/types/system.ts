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
  anthropic_api_key: { is_set: boolean; masked: string | null };
  anthropic_base_url: string;
  anthropic_model: string;
  anthropic_default_haiku_model: string;
  anthropic_default_opus_model: string;
  anthropic_default_sonnet_model: string;
  claude_code_subagent_model: string;
  agent_session_cleanup_delay_seconds: number;
  agent_max_concurrent_sessions: number;
}

export interface SystemConfigOptions {
  video_backends: string[];
  image_backends: string[];
  text_backends: string[];
  audio_backends?: string[];
  provider_names?: Record<string, string>;
}

export interface GetSystemConfigResponse {
  settings: SystemConfigSettings;
  options: SystemConfigOptions;
}

/** 任务类型桶键（docs/adr/0054）。代码内部术语，界面文案不直接呈现。 */
export type CapabilityBucket = "t2i" | "i2i" | "i2v" | "r2v";

/** 单一 media_type 的候选：默认层全量 + 各任务类型桶按能力过滤后的子集。 */
export interface MediaCandidates {
  default: string[];
  buckets: Partial<Record<CapabilityBucket, string[]>>;
}

export interface ModelCandidatesResponse {
  image: MediaCandidates;
  video: MediaCandidates;
  /** 仅含自定义供应商的显示名；内置供应商名由前端按 provider_id 本地化。 */
  provider_names: Record<string, string>;
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

/** 首次使用引导的「已看过」状态 —— 实例级，未设置视为未看过。 */
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
  anthropic_api_key?: string;
  anthropic_base_url?: string;
  anthropic_model?: string;
  anthropic_default_haiku_model?: string;
  anthropic_default_opus_model?: string;
  anthropic_default_sonnet_model?: string;
  claude_code_subagent_model?: string;
  agent_session_cleanup_delay_seconds?: number;
  agent_max_concurrent_sessions?: number;
}
