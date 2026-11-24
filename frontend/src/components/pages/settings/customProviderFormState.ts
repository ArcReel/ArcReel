import { uid } from "@/utils/id";
import { parseDurationInput } from "@/utils/duration_format";
import { parseMultiplier } from "@/utils/multiplier";
import type {
  CapabilityOverrides,
  CustomProviderInfo,
  CustomProviderModelInput,
  DiscoveredModel,
  EndpointDescriptor,
  EndpointKey,
  FetchedProviderModel,
  MatchedCatalogEntry,
  MediaType,
  PathOverrides,
  ProviderMultiplierInfo,
} from "@/types";
import { isComfyuiEndpoint, withDurationsOverride, type DiscoveryFormat } from "./customProviderHelpers";
import type { FetchSelection } from "./FetchModelsDialog";


export interface ModelRow {
  key: string;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  resolution: string;
  durations_text: string;
  multiplier_text: string;
  entry_binding: string | null;
  matched_entry: MatchedCatalogEntry | null | undefined;
  capability_overrides: CapabilityOverrides | null;
  path_overrides: PathOverrides | null;
  original_model_id: string;
  original_endpoint: EndpointKey;
}

export interface CustomProviderFormValue {
  displayName: string;
  pickedFormat: DiscoveryFormat | null;
  baseUrl: string;
  apiKey: string;
  noApiKey: boolean;
  models: ModelRow[];
  imageMaxWorkers: string;
  videoMaxWorkers: string;
  audioMaxWorkers: string;
  providerMultiplier: string;
}

export const DEFAULT_ENDPOINT = "openai-chat" as EndpointKey;

export const UNSET_ENDPOINT = "" as EndpointKey;

export function newModelRow(partial?: Partial<ModelRow>): ModelRow {
  const base = {
    key: uid(),
    model_id: "",
    display_name: "",
    endpoint: DEFAULT_ENDPOINT,
    is_default: false,
    is_enabled: true,
    resolution: "",
    durations_text: "",
    multiplier_text: "1",
    entry_binding: null,
    matched_entry: undefined,
    capability_overrides: null,
    path_overrides: null,
    ...partial,
  };
  return { ...base, original_model_id: base.model_id, original_endpoint: base.endpoint };
}

export function fetchedToRow(selection: FetchSelection, isDefault: boolean): ModelRow {
  const { model, endpoint, binding } = selection;
  return newModelRow({
    model_id: model.model_id,
    display_name: model.model_id,
    endpoint: endpoint ?? DEFAULT_ENDPOINT,
    is_default: isDefault,
    is_enabled: true,
    entry_binding: binding ? `${binding.maker}/${binding.name}` : null,
    matched_entry: binding ? undefined : model.matched_entry,
  });
}

export function discoveredToFetched(m: DiscoveredModel, mediaOf: (endpoint: EndpointKey) => MediaType | undefined): FetchedProviderModel {
  return {
    model_id: m.model_id,
    display_name: m.display_name,
    media_type: m.media_type ?? mediaOf(m.endpoint) ?? "text",
    recognized: m.recognized ?? !!m.matched_entry,
    model_key: null,
    matched_entry: m.matched_entry ?? null,
    endpoint: m.endpoint,
    endpoint_by_media_type: m.endpoint_by_media_type ?? null,
    is_default: m.is_default,
  };
}

function workersToStr(n?: number | null): string {
  return n != null ? String(n) : "";
}

export const DEFAULT_PROVIDER_MULTIPLIER: ProviderMultiplierInfo = { multiplier: 1, default: 1 };

export function newProviderDefaultMultiplier(isComfyui: boolean): number {
  return isComfyui ? 0 : DEFAULT_PROVIDER_MULTIPLIER.default;
}

function providerMultiplierToStr(info: ProviderMultiplierInfo): string {
  return info.multiplier === info.default ? "" : String(info.multiplier);
}

export function formFromProvider(
  provider: CustomProviderInfo,
  multiplier: ProviderMultiplierInfo = DEFAULT_PROVIDER_MULTIPLIER,
): CustomProviderFormValue {
  return {
    displayName: provider.display_name,
    pickedFormat: provider.discovery_format,
    baseUrl: provider.base_url,
    apiKey: "",
    noApiKey: false,
    models: [],
    imageMaxWorkers: workersToStr(provider.image_max_workers),
    videoMaxWorkers: workersToStr(provider.video_max_workers),
    audioMaxWorkers: workersToStr(provider.audio_max_workers),
    providerMultiplier: providerMultiplierToStr(multiplier),
  };
}

export function newProviderForm(initialBaseUrl?: string, initialEndpoint?: EndpointKey): CustomProviderFormValue {
  return {
    displayName: "",
    pickedFormat: null,
    baseUrl: initialBaseUrl ?? "",
    apiKey: "",
    noApiKey: false,
    models: initialEndpoint ? [newModelRow({ endpoint: initialEndpoint })] : [],
    imageMaxWorkers: "",
    videoMaxWorkers: "",
    audioMaxWorkers: "",
    providerMultiplier: "",
  };
}

export function parsePositiveInt(s: string): number | null | undefined {
  const trimmed = s.trim();
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isSafeInteger(n) && n >= 1 ? n : undefined;
}

export function parseOptionalMultiplier(s: string): number | null | undefined {
  return s.trim() ? parseMultiplier(s) : null;
}

export function matchedEntryFor(row: ModelRow): MatchedCatalogEntry | null | undefined {
  return row.model_id === row.original_model_id && row.endpoint === row.original_endpoint ? row.matched_entry : undefined;
}

export interface RowToInputOptions {
  durationsEditable: boolean;
}

export function rowToInput(r: ModelRow, { durationsEditable }: RowToInputOptions): CustomProviderModelInput {
  let overrides = r.capability_overrides;
  if (durationsEditable) {
    overrides = withDurationsOverride(overrides, parseDurationInput(r.durations_text) ?? undefined);
  }
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
    capability_overrides: overrides,
    path_overrides: r.path_overrides,
    multiplier: parseMultiplier(r.multiplier_text) ?? 1,
    entry_binding: r.entry_binding,
  };
}

export function effectiveModelRows(
  models: ModelRow[],
  catalog: EndpointDescriptor[] | null,
  isComfyui: boolean,
): ModelRow[] {
  if (catalog === null) return models;
  const firstComfyui = catalog.find(isComfyuiEndpoint)?.key;
  return models.map((row) => {
    const descriptor = catalog.find((item) => item.key === row.endpoint);
    if (descriptor !== undefined && isComfyuiEndpoint(descriptor) === isComfyui) return row;
    const endpoint = (isComfyui ? firstComfyui : DEFAULT_ENDPOINT) ?? UNSET_ENDPOINT;
    return { ...row, endpoint, is_default: false, capability_overrides: null, path_overrides: null };
  });
}
