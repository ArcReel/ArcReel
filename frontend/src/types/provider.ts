/**
 * 声音一致性三级标识，与 `lib/config/resolver.py::VoiceConsistency` 一一对应。
 * 档位值全部由服务端派生，前端只做渲染映射，不复制派生公式。
 */
export type VoiceConsistencyTier = "native" | "soft" | "none";

/**
 * 成片音轨形态，与 `lib/video_backends/base.py::VideoAudioMode` 一一对应：
 * `controllable` = 请求带音轨开关，用户的开/关意图能抵达供应商；`always_on` = 恒有声、无开关；
 * `always_off` = 该路径不产音轨、也无开关。三态由服务端从 backend 声明派生，前端不合成。
 */
export type VideoAudioControl = "controllable" | "always_on" | "always_off";

/**
 * 视频执行路径（能力桶），与 `lib/config/resolver.py::VideoCapability` 一一对应：
 * `i2v` 覆盖文生与图生首帧，`r2v` 是参考生视频。逐路径的能力位按它取值。
 */
export type VideoRoute = "i2v" | "r2v";

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
  // 成片音轨形态（可控 / 恒有声 / 恒无声），按执行路径各给一份：同一 model 在图生与参考生两条
  // 子路径上的请求形态可以不同（可灵 v3-omni 的多图主体子路径不带音轨开关，成片必然无声）。
  // 服务端从 backend 的 VideoCapabilities 派生，前端只取值、不合成三态；非视频 model 恒
  // always_off。见 utils/provider-models.ts::lookupVideoAudioControl。
  audio_track: VideoAudioControl;
  reference_route_audio_track: VideoAudioControl;
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
