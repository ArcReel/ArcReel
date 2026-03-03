export type SystemBackend = "aistudio" | "vertex";

export interface SecretFieldView {
  is_set: boolean;
  masked: string | null;
  source: "override" | "env" | "unset";
}

export interface TextFieldView {
  value: string | null;
  source: "override" | "env" | "unset";
}

export interface VertexCredentialView {
  is_set: boolean;
  filename: string | null;
  project_id: string | null;
}

export interface SystemRateLimitConfig {
  image_rpm: number;
  video_rpm: number;
  request_gap_seconds: number;
}

export interface SystemPerformanceConfig {
  storyboard_max_workers: number;
  video_max_workers: number;
}

export interface SystemConfigView {
  image_backend: SystemBackend;
  video_backend: SystemBackend;
  image_model: string;
  video_model: string;
  video_generate_audio: boolean;
  video_generate_audio_effective: boolean;
  video_generate_audio_editable: boolean;
  rate_limit: SystemRateLimitConfig;
  performance: SystemPerformanceConfig;
  gemini_api_key: SecretFieldView;
  anthropic_api_key: SecretFieldView;
  anthropic_base_url: TextFieldView;
  anthropic_model: TextFieldView;
  anthropic_default_haiku_model: TextFieldView;
  anthropic_default_opus_model: TextFieldView;
  anthropic_default_sonnet_model: TextFieldView;
  claude_code_subagent_model: TextFieldView;
  vertex_credentials: VertexCredentialView;
}

export interface SystemConfigOptions {
  image_models: string[];
  video_models: string[];
}

export interface GetSystemConfigResponse {
  config: SystemConfigView;
  options: SystemConfigOptions;
}

export interface SystemConnectionTestTarget {
  media_type: string;
  model: string;
}

export interface SystemConnectionTestRequest {
  provider: SystemBackend;
  image_backend?: SystemBackend;
  video_backend?: SystemBackend;
  image_model?: string;
  video_model?: string;
  gemini_api_key?: string | null;
}

export interface SystemConnectionTestResponse {
  ok: boolean;
  provider: SystemBackend;
  filename: string | null;
  project_id: string | null;
  checked_models: SystemConnectionTestTarget[];
  missing_models: string[];
  message: string;
}

export type SystemConfigPatch = Partial<{
  image_backend: SystemBackend | "" | null;
  video_backend: SystemBackend | "" | null;
  gemini_api_key: string | "" | null;
  anthropic_api_key: string | "" | null;
  anthropic_base_url: string | "" | null;
  anthropic_model: string | "" | null;
  anthropic_default_haiku_model: string | "" | null;
  anthropic_default_opus_model: string | "" | null;
  anthropic_default_sonnet_model: string | "" | null;
  claude_code_subagent_model: string | "" | null;
  image_model: string | "" | null;
  video_model: string | "" | null;
  video_generate_audio: boolean | null;
  gemini_image_rpm: number | null;
  gemini_video_rpm: number | null;
  gemini_request_gap: number | null;
  storyboard_max_workers: number | null;
  video_max_workers: number | null;
}>;
