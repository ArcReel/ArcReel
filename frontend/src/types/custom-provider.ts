export interface CustomProviderInfo {
  id: number;
  display_name: string;
  api_format: "openai" | "google";
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
}

export interface CustomProviderModelInfo {
  id: number;
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video";
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string | null;
  price_input: number | null;
  price_output: number | null;
  currency: string | null;
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video";
  is_default: boolean;
  is_enabled: boolean;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  api_format: "openai" | "google";
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
}

export interface CustomProviderModelInput {
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video";
  is_default: boolean;
  is_enabled: boolean;
  price_unit?: string;
  price_input?: number;
  price_output?: number;
  currency?: string;
}
