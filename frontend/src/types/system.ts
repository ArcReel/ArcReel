export interface SystemConfigSettings {
  default_video_backend: string;
  default_image_backend: string;
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
}

export interface GetSystemConfigResponse {
  settings: SystemConfigSettings;
  options: SystemConfigOptions;
}

export interface SystemConfigPatch {
  default_video_backend?: string;
  default_image_backend?: string;
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
