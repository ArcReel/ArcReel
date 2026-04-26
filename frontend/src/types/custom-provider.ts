export type EndpointKey =
  | "openai-chat"
  | "gemini-generate"
  | "openai-images"
  | "gemini-image"
  | "openai-video"
  | "newapi-video";

export type MediaType = "text" | "image" | "video";

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
}

export interface CustomProviderModelInfo {
  id: number;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string | null;
  price_input: number | null;
  price_output: number | null;
  currency: string | null;
  supported_durations: number[] | null;
  resolution: string | null;
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
}

export interface CustomProviderModelInput {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit?: string;
  price_input?: number;
  price_output?: number;
  currency?: string;
  supported_durations?: number[] | null;
  resolution?: string | null;
}

export const ENDPOINT_TO_MEDIA_TYPE: Record<EndpointKey, MediaType> = {
  "openai-chat": "text",
  "gemini-generate": "text",
  "openai-images": "image",
  "gemini-image": "image",
  "openai-video": "video",
  "newapi-video": "video",
};
