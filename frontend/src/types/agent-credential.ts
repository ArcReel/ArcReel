/**
 * Agent Anthropic 凭证 + 预设供应商目录类型。
 *
 * 与后端 server/routers/agent_config.py 的 Pydantic 模型对齐。
 */

export interface PresetProvider {
  id: string;
  display_name: string;
  icon_key: string;
  messages_url: string;
  discovery_url: string | null;
  default_model: string;
  suggested_models: string[];
  docs_url: string | null;
  api_key_url: string | null;
  notes: string | null;
  api_key_pattern: string | null;
  is_recommended: boolean;
}

export interface PresetProvidersResponse {
  providers: PresetProvider[];
  custom_sentinel_id: string;
}

export interface AgentCredential {
  id: number;
  preset_id: string;
  display_name: string;
  icon_key: string | null;
  base_url: string;
  api_key_masked: string;
  model: string | null;
  haiku_model: string | null;
  sonnet_model: string | null;
  opus_model: string | null;
  subagent_model: string | null;
  is_active: boolean;
  created_at: string | null;
}

export interface CreateAgentCredentialRequest {
  preset_id: string;
  display_name?: string | null;
  base_url?: string | null;
  api_key: string;
  model?: string | null;
  haiku_model?: string | null;
  sonnet_model?: string | null;
  opus_model?: string | null;
  subagent_model?: string | null;
  activate?: boolean | null;
}

export type UpdateAgentCredentialRequest = Partial<
  Omit<CreateAgentCredentialRequest, "preset_id" | "activate">
>;

export interface ProbeResult {
  success: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
}

export type DiagnosisCode =
  | "openai_compat_only"
  | "auth_failed"
  | "model_not_found"
  | "model_required"
  | "rate_limited"
  | "network"
  | "timeout"
  | "unknown";

export interface SuggestionAction {
  kind: "check_api_key" | "run_discovery" | "see_docs";
  suggested_value: string | null;
}

export interface TestConnectionResponse {
  overall: "ok" | "fail";
  messages_probe: ProbeResult;
  diagnosis: DiagnosisCode | null;
  suggestion: SuggestionAction | null;
  /** 本次实际请求的完整地址；与输入框下方的预览同值。 */
  messages_url: string;
}

export interface TestConnectionRequest {
  preset_id?: string | null;
  base_url?: string | null;
  api_key: string;
  model?: string | null;
}
