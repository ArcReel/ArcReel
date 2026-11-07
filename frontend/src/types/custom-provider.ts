// Endpoint keys use a string alias; backend ENDPOINT_REGISTRY is the source of truth. The frontend reads
// the runtime catalog through GET /api/v1/custom-providers/endpoints, so extending endpoints
// does not require updating a frontend union type.
export type EndpointKey = string;

export type MediaType = "text" | "image" | "video" | "audio";

export type ImageCap = "text_to_image" | "image_to_image";

/** Model discovery protocol: determines the interfaces used for model discovery and connection checks, not the model invocation protocol.
 *  For comfyui, model discovery is not applicable; only connection checking is performed. */
export type DiscoveryFormat = "openai" | "google" | "comfyui";

export interface EndpointDescriptor {
  key: string;
  media_type: MediaType;
  family: string;
  /** Implementation form: python = backend code, declarative = a declarative definition, comfyui = a ComfyUI workflow. */
  kind: "python" | "declarative" | "comfyui";
  /** Ownership: builtin = bundled and read-only, custom = user-created and editable/deletable. */
  source: "builtin" | "custom";
  display_name_key: string;
  /** Display name of a declarative endpoint (meta.name in the definition; proper names are not translated); null for Python built-ins, which use display_name_key. */
  display_name: string | null;
  request_method: string;
  request_path_template: string;
  /** Capability array for image endpoints; null for other media types. */
  image_capabilities: ImageCap[] | null;
  /** Whether execution actually forwards the last-frame constraint; meaningful only for video and always false otherwise. */
  end_image_capable: boolean;
  /** Size is fixed by the endpoint (a ComfyUI endpoint does not bind nodes for both width and height): aspect ratio and resolution selections have no effect. */
  size_fixed: boolean;
  /** Duration is fixed by the endpoint (frames has no bound node on a ComfyUI endpoint): determines which read-only wording is shown. */
  duration_fixed: boolean;
  /** Tiers are empty because no frame rate source can be read (frames is bound, but neither fps nor a manual frame rate is present): also controls wording only. */
  duration_frame_rate_missing: boolean;
  /** No tiers can be provided (frames is unbound, no frame rate source exists, or the frame rate cannot yield whole-second tiers): tiers are not editable, and ArcReel does not drive duration. */
  duration_tier_empty: boolean;
  /** The resolution this workflow actually produces when no resolution tier is selected; null for non-ComfyUI endpoints or unreadable literal dimensions. */
  native_resolution: string | null;
  /** Requests whose URLs may be overridden; an empty array means this endpoint does not allow overrides. */
  requests: EndpointRequest[];
}

/** One endpoint request with an overridable URL (docs/adr/0101). */
export interface EndpointRequest {
  /** Request name, used as a key in the model's path_overrides. */
  name: string;
  display_name_key: string;
  /** Default path after the normalized base_url, possibly containing placeholders such as {{ model }} and {{ task_id }}. */
  default_path: string;
  placeholders: string[];
  /** How base_url is normalized without overrides, used by the UI to reconstruct the final URL; overrides always resolve against the original value. */
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

/** Request name → complete replacement path. */
export type PathOverrides = Record<string, string>;

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: DiscoveryFormat;
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
  /** Concurrency limit per lane; null means unset (uses the global default). */
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
  /** Provider model multiplier: multiplied by the provider multiplier and applied to this model's reference cost. */
  multiplier: number;
  /** Entry binding "model maker/official model name"; null means match by model name. Must be returned unchanged when saving the entire table. */
  entry_binding: string | null;
  /** Matched catalog entry; null means no matching entry, so calls to this model are recorded as unpriced. */
  matched_entry: MatchedCatalogEntry | null;
  /** System-resolved video capabilities (all fields); null for non-video models. */
  system_capabilities: VideoCapabilityFlags | null;
  /** Sparse user overrides, merged with system_capabilities to obtain effective values; null when there are no overrides. */
  capability_overrides: CapabilityOverrides | null;
  /** Effective request URL overrides; null when none exist. */
  path_overrides: PathOverrides | null;
  /** Requests on the current endpoint whose URLs may be overridden. */
  requests: EndpointRequest[];
}

/** Summary of the catalog entry matched to a provider model. When `priced` is false, the entry has no usable price table and calls are also recorded as unpriced. */
export interface MatchedCatalogEntry {
  maker: string;
  name: string;
  media_type: MediaType;
  display_name: string;
  priced: boolean;
}

/** Reference audio transport accepted by the backend; none means the model has no voice input channel. */
export type ReferenceAudioMode = "none" | "direct";

/** Subset of backend VideoCapabilities used by the UI (the backend also has execution-only dimensions such as reference_audio_per_image,
 *  max_reference_audio_total_seconds, and max_prompt_chars, not declared here).
 *  Values are evaluated from both endpoint declarations and catalog entries. Reference image support is expressed by max_reference_images: 0 means not accepted,
 *  null means accepted but neither side declares a count (unrecorded); no separate boolean is used, because duplicate declarations could drift into
 *  contradictions such as claiming support with a zero limit. The same applies to reference audio count limits. */
export interface VideoCapabilityFlags {
  first_frame: boolean;
  last_frame: boolean;
  max_reference_images: number | null;
  reference_audio_mode: ReferenceAudioMode;
  max_reference_audio_count: number | null;
}

/** Output audio track mode: controllable / always on / always off. */
export type VideoAudioMode = "controllable" | "always_on" | "always_off";

/** Video channel fact dimensions in capability overrides (input modes, reference limits, prompt length limit, audio track mode). */
export interface VideoChannelFactOverrides extends VideoCapabilityFlags {
  text_to_video: boolean;
  max_reference_audio_total_seconds: number | null;
  max_prompt_chars: number | null;
  audio_track: VideoAudioMode;
  reference_route_audio_track: VideoAudioMode | null;
}

/** Model fact dimensions in capability overrides, exposed by media type: separate groups for text, image, and video. */
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
  };
}

/** Sparse override dictionary: an absent key follows the entry and backend declarations; a present key replaces the entire dimension. */
export type CapabilityOverrides = Partial<VideoChannelFactOverrides & ModelFactOverrides>;

/** Model discovery response. When ``not_applicable`` is true, ``models`` is always empty and ``reason`` is a ready-to-display explanation:
 *  this protocol has no such step; it is not a failed discovery that found nothing. */
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
  /** Maximum output length from discovery (currently only text models using the Google protocol); null when unavailable. The provider form does not read it. */
  max_output_tokens?: number | null;
  /** Catalog entry matched by model name; null means no match, and calls are recorded as unpriced. */
  matched_entry?: MatchedCatalogEntry | null;
  /** Media type of the endpoint. */
  media_type?: MediaType;
  /** A catalog entry was matched (recognized). */
  recognized?: boolean;
  /** Default endpoint for each media type when switching to an entry of another media type. */
  endpoint_by_media_type?: Record<MediaType, EndpointKey> | null;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  discovery_format: DiscoveryFormat;
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
  /** Concurrency limit per lane; omitted or null means unset (uses the global default). */
  image_max_workers?: number | null;
  video_max_workers?: number | null;
  audio_max_workers?: number | null;
}

export interface CustomProviderFullUpdateRequest {
  display_name: string;
  base_url: string;
  api_key?: string;
  /** Omission leaves models unchanged: the detail page saves models individually through the provider model API; returning the full table would overwrite intervening row-level enablement and settings. */
  models?: CustomProviderModelInput[];
  /** PUT is authoritative for concurrency limits: required, with null clearing the limit (using the global default). The server treats omitted fields as
   *  cleared, so the type makes them required to prevent accidental deletion of existing configuration through silent omission. */
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
  /** Saving the model list replaces it entirely: omitting this field clears existing overrides, so it must be returned unchanged when editing existing models. */
  capability_overrides?: CapabilityOverrides | null;
  /** Request URL overrides, with the same full-replacement semantics as capability_overrides; must be returned unchanged when editing existing models. */
  path_overrides?: PathOverrides | null;
  /** Provider model multiplier, a finite nonnegative number; defaults to 1 when omitted. */
  multiplier?: number;
  /** Entry binding; like overrides, must be returned unchanged when editing existing models. */
  entry_binding?: string | null;
}

/** Provider multiplier: `multiplier` is the effective value, and `default` applies when unset. */
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
