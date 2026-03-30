export interface ProviderInfo {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types: string[];
  capabilities: string[];
  configured_keys: string[];
  missing_keys: string[];
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

export interface ProviderConfigDetail {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types?: string[];
  fields: ProviderField[];
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
  is_active: boolean;
  created_at: string;
}

export type CallType = "image" | "video" | "text";

export interface UsageStat {
  provider: string;
  call_type: CallType;
  total_calls: number;
  success_calls: number;
  total_cost_usd: number;
  total_duration_seconds?: number;
}

export interface UsageStatsResponse {
  stats: UsageStat[];
  period: { start: string; end: string };
}
