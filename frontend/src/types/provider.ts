import type { CapabilityOverrides, EndpointRequest, MatchedCatalogEntry, PathOverrides } from "./custom-provider";

/**
 * Three-tier voice consistency indicator, matching `lib/config/resolver.py::VoiceConsistency` exactly.
 * All tiers are derived by the server; the frontend maps them to presentation without copying derivation formulas.
 */
export type VoiceConsistencyTier = "native" | "soft" | "none";

/**
 * Output audio track mode, matching `packages/arcreel-market-core/src/arcreel_market_core/video_backend_contract.py::VideoAudioMode` exactly:
 * `controllable` = the request carries an audio switch that forwards the user's on/off choice to the provider; `always_on` = always has audio, without a switch;
 * `always_off` = this route produces no audio track and has no switch. The server derives all three states from backend declarations; the frontend does not synthesize them.
 */
export type VideoAudioControl = "controllable" | "always_on" | "always_off";

/**
 * Video execution route (task type bucket), matching `lib/config/resolver.py::VideoGenerationType` exactly:
 * `i2v` covers text-to-video and first-frame image-to-video; `r2v` is reference-to-video. Per-route capability flags use this value.
 */
export type VideoRoute = "i2v" | "r2v";

export interface ModelInfoResponse {
  display_name: string;
  media_type: string;
  capabilities: string[];
  default: boolean;
  // Full duration set declared by the model. Coupled resolution / reference-image constraints are outside the catalog: narrowed results are calculated by video-capabilities
  // endpoints for the context (see types/project.ts::DurationConstraints); the frontend does not own the rules.
  supported_durations: number[];
  resolutions: string[];
  // Output audio mode (controllable / always on / always off), provided per execution route: the same model may use different request shapes for image and reference
  // video subroutes (Kling v3-omni's multi-image subject subroute has no audio switch and always produces silent output).
  // The server derives this from backend VideoCapabilities; the frontend only reads values and never synthesizes the states. Non-video models always use
  // always_off. See utils/provider-models.ts::lookupVideoAudioControl.
  audio_track: VideoAudioControl;
  reference_route_audio_track: VideoAudioControl;
  // Server-derived voice consistency tier without project context (generation_mode is unknown, so native is always downgraded).
  // Pages with project context use the same-named field from /projects/{name}/video-capabilities instead of this one.
  voice_consistency: VoiceConsistencyTier;
}

export interface ProviderInfo {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types: string[];
  capabilities: string[];
  /** Number of credentials (called Keys in the UI); 0 when no credentials exist. */
  credential_count: number;
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

// Secret inputs rendered by the credential form (derived by the backend from required ∩ secret ∩ credential keys, the single source of truth).
// Single-secret provider → [api_key]; Kling → [access_key, secret_key].
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
  // Whether credentials support a custom base_url (derived by the backend from optional_keys, the single source of truth)
  supports_base_url: boolean;
  // Secret fields to render in the credential form, in order
  secret_fields: CredentialSecretField[];
  // Alternative credential groups: completing any group (all its fields filled) makes credentials complete; a single group (most
  // providers) is equivalent to the previous requirement that all secret_fields be filled. Kling uses [["api_key"], ["access_key", "secret_key"]].
  secret_field_groups: string[][];
}

export interface ConnectivityCheckResult {
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
  // Two independently masked secret fields (Kling); null/absent for other providers
  access_key_masked?: string | null;
  secret_key_masked?: string | null;
  is_active: boolean;
  created_at: string;
}

export type CallType = "image" | "video" | "text" | "audio";


/** Unit-price range for one meter in a price summary; `per` is the quantity corresponding to the unit price (1000000 for per-million tokens; 1 for per-image or per-second). */
export interface PriceSummaryItem {
  meter: string;
  min_price: number;
  max_price: number;
  per: number;
}

export interface PriceSummary {
  currency: string;
  items: PriceSummaryItem[];
}

/**
 * Provider model status flags: `discontinued` is offline; `retiring` is due to go offline (see `lifecycle` for the shutdown date);
 * `unpriced` records call costs as unpriced; `modified` means the user changed the corresponding model information, which no longer follows official updates.
 */
export type ProviderModelFlag = "discontinued" | "retiring" | "unpriced" | "modified";

/**
 * Lifecycle of the entry matched to the provider model as of today (UTC date). `successor` is the official successor's "model maker/official model name";
 * models without a matching entry are `available`, with null dates and successor.
 */
export interface ProviderModelLifecycle {
  status: "available" | "retiring" | "discontinued";
  /** ISO date (YYYY-MM-DD). */
  shutdown_date: string | null;
  successor: string | null;
}

/** One provider detail model-list row (GET /providers/{id}/models), shared by built-in and custom providers. */
export interface ProviderModelItem {
  /** Model key used to reference this model in settings and projects. */
  model_id: string;
  api_model_name: string;
  /** The UI shows the model ID when no display name exists. */
  display_name: string | null;
  media_type: "text" | "image" | "video" | "audio";
  enabled: boolean;
  is_default: boolean;
  /** An officially listed model of a built-in provider; may only be enabled or disabled. */
  official: boolean;
  flags: ProviderModelFlag[];
  price: PriceSummary | null;
  /** Discontinued models remain in the list but are never candidates and cannot be called. */
  lifecycle: ProviderModelLifecycle;
  /** Endpoint key for a custom provider model; null for built-in providers. */
  endpoint: string | null;
  /** Minimum app version annotated on an officially listed model; null when absent or when the model is not officially listed. */
  min_app_version: string | null;
  /**
   * The app version meets `min_app_version`. Models that do not meet it are placed at the end, disabled, excluded from defaults and candidates, and cannot be called;
   * they cannot be enabled and have no model settings to open.
   */
  min_app_version_satisfied: boolean;
}

/** Provider model list (GET /providers/{id}/models); when `discoverable` is true, models can be fetched. */
export interface ProviderModelsResponse {
  models: ProviderModelItem[];
  discoverable: boolean;
}

/**
 * One model returned by Fetch Models (POST /providers/{id}/models/discover).
 * - `recognized`: for custom providers, the name matched an entry; for built-in providers, it matched an officially listed model, whose key is `model_key`.
 * - `media_type`: taken from a matching entry, otherwise inferred from the name.
 * - `endpoint`, `endpoint_by_media_type`: the custom provider's default endpoint and per-media-type default endpoints when switching to entries of other media types;
 *   null for built-in providers.
 */
export interface FetchedProviderModel {
  model_id: string;
  display_name: string;
  media_type: ProviderModelItem["media_type"];
  recognized: boolean;
  model_key: string | null;
  matched_entry: MatchedCatalogEntry | null;
  endpoint: string | null;
  endpoint_by_media_type: Partial<Record<ProviderModelItem["media_type"], string>> | null;
  is_default: boolean;
}

/** Fetch Models result; when `not_applicable` is true, this provider has no model-list API and `reason` is ready-to-display explanatory text. */
export interface FetchProviderModelsResponse {
  models: FetchedProviderModel[];
  not_applicable: boolean;
  reason: string | null;
}

/** Add a model by ID (POST /providers/{id}/models). `endpoint` applies only to custom providers and is inferred from the name when omitted. */
export interface AddProviderModelRequest {
  model_id: string;
  media_type?: ProviderModelItem["media_type"];
  entry_binding?: string | null;
  endpoint?: string | null;
}

export interface AddProviderModelResponse {
  model: ProviderModelItem;
  /** Matched an officially listed model; adding enables it. */
  official: boolean;
}

/** Capability override dimension key. */
export type CapabilityKey = keyof CapabilityOverrides;

/**
 * Parameters supported by this provider (GET /providers/{id}/models/{model}):
 * `base` is the runtime value without overrides (evaluated from the catalog entry and backend); `null` means neither declares it (unrecorded);
 * `overrides` contains active capability overrides; `effective` contains effective values after overrides; `editable` lists editable dimensions in UI order,
 * empty for officially listed models; `displayed` lists all displayed dimensions in UI order, with dimensions outside `editable` read-only.
 */
export interface ProviderModelCapabilities {
  media_type: ProviderModelItem["media_type"];
  editable: CapabilityKey[];
  displayed: CapabilityKey[];
  base: Partial<Record<CapabilityKey, unknown>>;
  overrides: CapabilityOverrides;
  effective: Partial<Record<CapabilityKey, unknown>>;
}

/** A price table selection dimension value: numbers for context tier lower bounds and duration seconds, booleans for flags such as audio, and strings for resolution and service tier. */
export type PriceDimensionValue = string | number | boolean;

/**
 * Catalog entry price table (catalog data format): single-currency, composed of meters, each divided into tiers by selection dimensions,
 * with one condition → unit price row per tier. `defaults` supplies missing request dimension values; `free_allowance` is the free allowance per request.
 */
export interface CatalogPriceTable {
  currency: string;
  meters: {
    meter: string;
    dims: string[];
    rows: { when: Record<string, PriceDimensionValue>; price: number }[];
    defaults?: Record<string, PriceDimensionValue>;
    free_allowance?: number;
    cjk_double?: boolean;
  }[];
}

/** Who maintains model information: follows official data, modified by the user, or user-created without official data. */
export type CatalogEntrySource = "official" | "modified" | "user_created";

/** Entry identity: "model maker/official model name", further distinguished by media type for multimodal models. */
export interface CatalogEntryKey {
  maker: string;
  name: string;
  media_type: ProviderModelItem["media_type"];
}

/**
 * Resolved entry matched to a provider model, shown in the sheet's lower Model Information section.
 * `official` contains official values when the user has made changes; `shared_with` lists other providers using this entry.
 */
export interface ProviderModelEntryInfo extends CatalogEntryKey {
  display_name: string;
  aliases: string[];
  discontinued: boolean;
  context_length: number | null;
  facts: Record<string, unknown>;
  /** Price table; absent price tables cause call costs to be recorded as unpriced. */
  price: CatalogPriceTable | null;
  source: CatalogEntrySource;
  official: { display_name: string; facts: Record<string, unknown>; price: CatalogPriceTable | null } | null;
  shared_with: { provider_id: string; display_name: string }[];
}

/** Model Information changes: identity is the entry read by the sheet, and `facts` includes only model facts to change. */
export interface CatalogEntryUpdate extends CatalogEntryKey {
  display_name?: string;
  facts?: Record<string, unknown>;
  /** The complete price table, replaced entirely; `null` clears it. An absent key leaves it unchanged. */
  price?: CatalogPriceTable | null;
}

/**
 * A manually entered new entry (a model absent from the catalog): missing `facts` fields use media-type defaults, and omitted `display_name` uses the model name;
 * `price` is a price table in catalog data format; omission records call costs as unpriced.
 */
export interface NewCatalogEntry extends CatalogEntryKey {
  display_name?: string;
  facts?: Record<string, unknown>;
  price?: CatalogPriceTable;
}

/** Contents of the provider model sheet. */
export interface ProviderModelDetail {
  model: ProviderModelItem;
  /** Provider model multiplier; null for officially listed models (not displayed). */
  multiplier: number | null;
  provider_multiplier: number;
  entry: ProviderModelEntryInfo | null;
  /** User-specified entry binding ("model maker/official model name"); null for name-matched or officially listed models. */
  entry_binding: string | null;
  capabilities: ProviderModelCapabilities;
  /** Endpoint: present for custom provider models and user-added models of built-in providers; null for officially listed models, whose endpoints follow official data. */
  endpoint: string | null;
  /** The following fields apply only to custom provider models. */
  resolution: string | null;
  path_overrides: PathOverrides | null;
  requests: EndpointRequest[];
  /** Endpoint options for user-added models of a built-in provider (that provider's built-in endpoints for this media type); empty otherwise. */
  endpoint_options: BuiltinEndpointOption[];
}

/** One built-in provider endpoint option. An empty `display_name_key` displays `display_name` instead. */
export interface BuiltinEndpointOption {
  key: string;
  display_name_key: string;
  display_name: string | null;
}

/** Save provider settings and model information from the provider model sheet; omitted fields remain unchanged. */
export interface ProviderModelSettingsUpdate {
  enabled?: boolean;
  is_default?: boolean;
  model_id?: string;
  multiplier?: number;
  capability_overrides?: CapabilityOverrides | null;
  endpoint?: string;
  path_overrides?: PathOverrides | null;
  resolution?: string | null;
  /** Modify the corresponding model information and save it as a user copy; the model no longer follows official updates. */
  entry?: CatalogEntryUpdate;
  /** Bind another catalog entry of the same media type ("model maker/official model name"); `null` removes the binding and returns to name matching. */
  entry_binding?: string | null;
  /** Manual entry: create an entry and bind it during this save. Takes precedence when sent together with `entry_binding`. */
  new_entry?: NewCatalogEntry;
}

/** Global settings keys and projects referencing a provider model. */
export interface ProviderModelReferences {
  settings: string[];
  projects: { name: string; title: string | null }[];
}
