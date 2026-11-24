import type { VoiceConsistencyTier } from "./provider";

export type EndpointKey = string;

export type MediaType = "text" | "image" | "video" | "audio";

export type ImageCap = "text_to_image" | "image_to_image";

export type DiscoveryFormat = "openai" | "google" | "comfyui";

export interface EndpointDescriptor {
  key: string;
  media_type: MediaType;
  family: string;
  kind: "python" | "declarative" | "comfyui";
  source: "builtin" | "custom";
  display_name_key: string;
  display_name: string | null;
  request_method: string;
  request_path_template: string;
  image_capabilities: ImageCap[] | null;
  end_image_capable: boolean;
  size_fixed: boolean;
  duration_fixed: boolean;
  duration_frame_rate_missing: boolean;
  duration_tier_empty: boolean;
  native_resolution: string | null;
  requests: EndpointRequest[];
}

export interface EndpointRequest {
  name: string;
  display_name_key: string;
  default_path: string;
  placeholders: string[];
  base_url_rule:
    | "as_is"
    | "strip_version"
    | "append_v1"
    | "ensure_api_v3"
    | "ensure_ent_v2"
    | "ensure_v1"
    | "dashscope_api_v1"
    | "strip_messages";
}

export type PathOverrides = Record<string, string>;

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: DiscoveryFormat;
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
  image_max_workers: number | null;
  video_max_workers: number | null;
  audio_max_workers: number | null;
}

export interface CustomProviderModelInfo {
  id: number;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  resolution: string | null;
  qualities: string[];
  multiplier: number;
  entry_binding: string | null;
  matched_entry: MatchedCatalogEntry | null;
  system_capabilities: VideoCapabilityFlags | null;
  audio_track?: VideoAudioMode | null;
  reference_route_audio_track?: VideoAudioMode | null;
  voice_consistency?: VoiceConsistencyTier | null;
  capability_overrides: CapabilityOverrides | null;
  path_overrides: PathOverrides | null;
  requests: EndpointRequest[];
}

export interface MatchedCatalogEntry {
  maker: string;
  name: string;
  media_type: MediaType;
  display_name: string;
  priced: boolean;
}

export type ReferenceAudioMode = "none" | "direct";

export interface VideoCapabilityFlags {
  first_frame: boolean;
  last_frame: boolean;
  max_reference_images: number | null;
  reference_audio_mode: ReferenceAudioMode;
  max_reference_audio_count: number | null;
}

export type VideoAudioMode = "controllable" | "always_on" | "always_off";

export interface VideoChannelFactOverrides extends VideoCapabilityFlags {
  text_to_video: boolean;
  max_reference_audio_total_seconds: number | null;
  max_prompt_chars: number | null;
  audio_track: VideoAudioMode;
  reference_route_audio_track: VideoAudioMode | null;
}

export interface ModelFactOverrides {
  image_input: boolean;
  structured_output: boolean;
  max_output_tokens: number;
  text_to_image: boolean;
  image_to_image: boolean;
  durations: number[];
  resolutions: string[];
  duration_constraints: {
    by_resolution?: Record<string, number[]>;
    with_reference_images?: number[];
    with_end_frame?: number[];
  };
}

export type CapabilityOverrides = Partial<VideoChannelFactOverrides & ModelFactOverrides>;

export interface DiscoverModelsResponse {
  models: DiscoveredModel[];
  not_applicable: boolean;
  reason: string | null;
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  max_output_tokens?: number | null;
  matched_entry?: MatchedCatalogEntry | null;
  media_type?: MediaType;
  recognized?: boolean;
  endpoint_by_media_type?: Record<MediaType, EndpointKey> | null;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  discovery_format: DiscoveryFormat;
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
  image_max_workers?: number | null;
  video_max_workers?: number | null;
  audio_max_workers?: number | null;
}

export interface CustomProviderFullUpdateRequest {
  display_name: string;
  base_url: string;
  api_key?: string;
  models?: CustomProviderModelInput[];
  image_max_workers: number | null;
  video_max_workers: number | null;
  audio_max_workers: number | null;
}

export interface CustomProviderModelInput {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  resolution?: string | null;
  capability_overrides?: CapabilityOverrides | null;
  path_overrides?: PathOverrides | null;
  multiplier?: number;
  entry_binding?: string | null;
}

export interface ProviderMultiplierInfo {
  multiplier: number;
  default: number;
}

export interface AnthropicDiscoverRequest {
  base_url?: string;
  api_key?: string;
}

export interface AnthropicDiscoverResponse {
  models: Array<{
    model_id: string;
    display_name: string;
    endpoint: string;
    is_default: boolean;
    is_enabled: boolean;
  }>;
}
