/**
 * 声音一致性三级标识，与 `lib/config/resolver.py::VoiceConsistency` 一一对应。
 * 档位值全部由服务端派生，前端只做渲染映射，不复制派生公式。
 */
export type VoiceConsistencyTier = "native" | "soft" | "none";

export interface ModelInfoResponse {
  display_name: string;
  media_type: string;
  capabilities: string[];
  default: boolean;
  supported_durations: number[];
  duration_resolution_constraints: Record<string, number[]>;
  // 使用参考图时允许的时长；空 = 参考图路径不额外约束时长。
  reference_image_durations?: number[];
  resolutions: string[];
  // 视频 model 是否带音轨（非视频 model 恒 false）。不等于「音轨开关可控」——AI Studio Veo /
  // Grok Imagine 恒有声但未声明 generate_audio token，仍为 true（见 capabilities 语义注）。
  has_audio_track: boolean;
  // 请求参数能否控制音轨开关（非视频 model 恒 false）。与 has_audio_track 合起来分出
  // 可控 / 恒有声 / 恒无声三态，见 utils/provider-models.ts::lookupVideoAudioControl。
  audio_switch_controllable: boolean;
  // 无项目上下文时的声音一致性档位，服务端派生（generation_mode 未知，native 恒降格）。
  // 有项目上下文的页面改用 /projects/{name}/video-capabilities 的同名字段，不读这里。
  voice_consistency: VoiceConsistencyTier;
}

export interface ProviderInfo {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types: string[];
  capabilities: string[];
  configured_keys: string[];
  missing_keys: string[];
  models: Record<string, ModelInfoResponse>;
  group?: "builtin" | "own";
}

export interface ProviderField {
  key: string;
  label: string;
  type: "secret" | "text" | "url" | "number" | "file";
  required: boolean;
  is_set: boolean;
  value?: string;
  value_masked?: string;
  placeholder?: string;
}

// 凭证表单需渲染的 secret 输入字段（后端按 required ∩ secret ∩ 凭证键派生，单一真相源）。
// 单 secret provider → [api_key]；可灵 → [access_key, secret_key]。
export interface CredentialSecretField {
  key: string;
  label: string;
}

export interface ProviderConfigDetail {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types?: string[];
  fields: ProviderField[];
  // 凭证是否支持自定义 base_url（后端按 optional_keys 派生，单一真相源）
  supports_base_url: boolean;
  // 凭证表单应渲染的 secret 字段（有序）
  secret_fields: CredentialSecretField[];
  // 凭证「二选一」分组：满足任一组（组内字段全填）即视为凭证完整；单组场景（绝大多数
  // provider）等价于「全部 secret_fields 必填」的旧语义。可灵为 [["api_key"], ["access_key", "secret_key"]]。
  secret_field_groups: string[][];
}

export interface ProviderTestResult {
  success: boolean;
  available_models: string[];
  message: string;
}

export interface ProviderCredential {
  id: number;
  provider: string;
  name: string;
  api_key_masked: string | null;
  credentials_filename: string | null;
  base_url: string | null;
  // 逐字段独立脱敏的双 secret（可灵）；其余 provider 为 null/缺省
  access_key_masked?: string | null;
  secret_key_masked?: string | null;
  is_active: boolean;
  created_at: string;
}

export type CallType = "image" | "video" | "text" | "audio";

export interface UsageStat {
  provider: string;
  display_name?: string;
  call_type: CallType;
  total_calls: number;
  success_calls: number;
  total_cost_usd: number;
  cost_by_currency: Record<string, number>;
  total_duration_seconds?: number;
}

export interface UsageStatsResponse {
  stats: UsageStat[];
  period: { start: string; end: string };
}
