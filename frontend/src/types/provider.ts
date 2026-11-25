import type { CapabilityOverrides, EndpointRequest, MatchedCatalogEntry, PathOverrides } from "./custom-provider";

export type VoiceConsistencyTier = "native" | "soft" | "none";

export type VideoAudioControl = "controllable" | "always_on" | "always_off";

export type VideoRoute = "i2v" | "r2v";

export interface ModelInfoResponse {
  display_name: string;
  media_type: string;
  capabilities: string[];
  default: boolean;
  supported_durations: number[];
  resolutions: string[];
  qualities: string[];
  audio_track: VideoAudioControl;
  reference_route_audio_track: VideoAudioControl;
  voice_consistency: VoiceConsistencyTier;
}

export interface ProviderInfo {
  id: string;
  display_name: string;
  description: string;
  status: "ready" | "unconfigured" | "error";
  media_types: string[];
  capabilities: string[];
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
  supports_base_url: boolean;
  secret_fields: CredentialSecretField[];
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
  access_key_masked?: string | null;
  secret_key_masked?: string | null;
  is_active: boolean;
  created_at: string;
}

export type CallType = "image" | "video" | "text" | "audio";


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

export type ProviderModelFlag = "discontinued" | "retiring" | "unpriced" | "modified";

export interface ProviderModelLifecycle {
  status: "available" | "retiring" | "discontinued";
  shutdown_date: string | null;
  successor: string | null;
}

export interface ProviderModelItem {
  model_id: string;
  api_model_name: string;
  display_name: string | null;
  media_type: "text" | "image" | "video" | "audio";
  enabled: boolean;
  is_default: boolean;
  official: boolean;
  flags: ProviderModelFlag[];
  price: PriceSummary | null;
  lifecycle: ProviderModelLifecycle;
  endpoint: string | null;
  min_app_version: string | null;
  min_app_version_satisfied: boolean;
}

export interface ProviderModelsResponse {
  models: ProviderModelItem[];
  discoverable: boolean;
}

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

export interface FetchProviderModelsResponse {
  models: FetchedProviderModel[];
  not_applicable: boolean;
  reason: string | null;
}

export interface AddProviderModelRequest {
  model_id: string;
  media_type?: ProviderModelItem["media_type"];
  entry_binding?: string | null;
  endpoint?: string | null;
}

export interface AddProviderModelResponse {
  model: ProviderModelItem;
  official: boolean;
}

export type CapabilityKey = keyof CapabilityOverrides;

type DisplayedCapability = CapabilityKey | "audio_switch_controllable" | "reference_route_audio_switch_controllable";

export interface ProviderModelCapabilities {
  media_type: ProviderModelItem["media_type"];
  editable: CapabilityKey[];
  displayed: DisplayedCapability[];
  base: Partial<Record<DisplayedCapability, unknown>> & { duration_fixed?: boolean };
  overrides: CapabilityOverrides;
  effective: Partial<Record<DisplayedCapability, unknown>>;
}

export type PriceDimensionValue = string | number | boolean;

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

export type CatalogEntrySource = "official" | "modified" | "user_created";

export interface CatalogEntryKey {
  maker: string;
  name: string;
  media_type: ProviderModelItem["media_type"];
}

export interface ProviderModelEntryInfo extends CatalogEntryKey {
  display_name: string;
  aliases: string[];
  discontinued: boolean;
  context_length: number | null;
  facts: Record<string, unknown>;
  price: CatalogPriceTable | null;
  source: CatalogEntrySource;
  official: { display_name: string; facts: Record<string, unknown>; price: CatalogPriceTable | null } | null;
  shared_with: { provider_id: string; display_name: string }[];
}

export interface CatalogEntryUpdate extends CatalogEntryKey {
  display_name?: string;
  facts?: Record<string, unknown>;
  price?: CatalogPriceTable | null;
}

export interface NewCatalogEntry extends CatalogEntryKey {
  display_name?: string;
  facts?: Record<string, unknown>;
  price?: CatalogPriceTable;
}

export interface ProviderModelDetail {
  model: ProviderModelItem;
  multiplier: number | null;
  provider_multiplier: number;
  entry: ProviderModelEntryInfo | null;
  entry_binding: string | null;
  capabilities: ProviderModelCapabilities;
  endpoint: string | null;
  resolution: string | null;
  path_overrides: PathOverrides | null;
  requests: EndpointRequest[];
  endpoint_options: BuiltinEndpointOption[];
}

export interface BuiltinEndpointOption {
  key: string;
  display_name_key: string;
  display_name: string | null;
}

export interface ProviderModelSettingsUpdate {
  enabled?: boolean;
  is_default?: boolean;
  model_id?: string;
  multiplier?: number;
  capability_overrides?: CapabilityOverrides | null;
  endpoint?: string;
  path_overrides?: PathOverrides | null;
  resolution?: string | null;
  entry?: CatalogEntryUpdate;
  entry_binding?: string | null;
  new_entry?: NewCatalogEntry;
}

export interface ProviderModelReferences {
  settings: string[];
  projects: { name: string; title: string | null }[];
}
