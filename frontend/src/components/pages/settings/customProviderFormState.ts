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

// ---------------------------------------------------------------------------
// Custom-provider edit-unit contents; saved providers and unsaved changes compare using this shape.
// ---------------------------------------------------------------------------

export interface ModelRow {
  /** Randomly generated stable row identities. */
  key: string;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  resolution: string; // Empty strings = null.
  // Raw duration text, editable only for ComfyUI video rows: submissions write capability durations overrides; empty removes keys,
  // following workflow-native durations. Load from overrides; other rows retain raw values but ignore them on submission.
  durations_text: string;
  // Raw provider-model multipliers, parsed before submission; finite nonnegative numbers required.
  multiplier_text: string;
  // Entry bindings vendor/official model name, present when discovery matches unrecognized models through user selections.
  entry_binding: string | null;
  // Server entries matched by (model_id, media type, entry bindings); null = unmatched/unpriced,
  // undefined = unknown (new manual rows, selected matches, changed IDs/endpoints, known only after saves).
  matched_entry: MatchedCatalogEntry | null | undefined;
  capability_overrides: CapabilityOverrides | null;
  // Request-address overrides (request name → path); null follows all endpoint defaults.
  path_overrides: PathOverrides | null;
  // Row-creation IDs/endpoints: discovered matches apply only to this pair; changing either makes results unknown.
  original_model_id: string;
  original_endpoint: EndpointKey;
}

export interface CustomProviderFormValue {
  displayName: string;
  /** User-selected protocols. Never-selected differs from selected openai; only former lets wired endpoints determine protocols. */
  pickedFormat: DiscoveryFormat | null;
  baseUrl: string;
  /** Newly entered secrets; blank edits retain existing secrets. */
  apiKey: string;
  /** No key required: local/no-credential APIs save empty keys, disabling required creation validation. */
  noApiKey: boolean;
  models: ModelRow[];
  imageMaxWorkers: string;
  videoMaxWorkers: string;
  audioMaxWorkers: string;
  /** Provider multipliers; empty means defaults, submitting null on save. */
  providerMultiplier: string;
}

//: Default endpoints for new rows; switching away from ComfyUI resets incompatible rows here too.
export const DEFAULT_ENDPOINT = "openai-chat" as EndpointKey;

//: Rows without endpoints. Switching to ComfyUI without any compatible endpoints leaves rows here:
//: selectors show unselected, blocking saves until users import/select endpoints.
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

/**
 * Checked Fetch models entries become new form rows. Selected matching models store bindings, whose server-resolved results remain
 * unknown until saving; callers supply `isDefault` per media type.
 */
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

/** Convert creation-form discovery results into Fetch models rows; name-matched custom-provider entries count as recognized. */
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

// Concurrency limits use controlled numeric strings; empty means unset (null/global defaults).
function workersToStr(n?: number | null): string {
  return n != null ? String(n) : "";
}

/** Unset provider multipliers; creation forms lacking server defaults display this value. */
export const DEFAULT_PROVIDER_MULTIPLIER: ProviderMultiplierInfo = { multiplier: 1, default: 1 };

/**
 * New providers lack IDs/server multiplier defaults; mirror protocol defaults: ComfyUI 0, others 1
 * (matching default_provider_multiplier in lib/billing/multipliers.py).
 */
export function newProviderDefaultMultiplier(isComfyui: boolean): number {
  return isComfyui ? 0 : DEFAULT_PROVIDER_MULTIPLIER.default;
}

// Default-equivalent provider multipliers display blank to follow defaults; saving blank restores defaults.
function providerMultiplierToStr(info: ProviderMultiplierInfo): string {
  return info.multiplier === info.default ? "" : String(info.multiplier);
}

/** Saved-provider form contents; existing models save individually in lists/Sheets, never enter forms. */
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

/** Initial creation forms prefill API addresses from wired Endpoints, creating one row for selected endpoints. */
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

// Empty means unset (null); otherwise require positive integers (≥1). undefined indicates invalid
// inputs (0, fractions, scientific notation, negatives, nondigits), blocked/explained on save; never parseInt
// silently truncating "1.5"→1 or "1e3"→1 into incorrect settings. Zero is invalid user input.
export function parsePositiveInt(s: string): number | null | undefined {
  const trimmed = s.trim();
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isSafeInteger(n) && n >= 1 ? n : undefined;
}

/** Provider multipliers: blank restores defaults (null); otherwise parseMultiplier, returning undefined for invalid inputs. */
export function parseOptionalMultiplier(s: string): number | null | undefined {
  return s.trim() ? parseMultiplier(s) : null;
}

/**
 * Entries matching current row edits. Servers match model IDs and endpoint media types; changing either
 * requires reevaluation. Frontend never guesses, reporting unknown (undefined); reverting restores loaded results.
 */
export function matchedEntryFor(row: ModelRow): MatchedCatalogEntry | null | undefined {
  return row.model_id === row.original_model_id && row.endpoint === row.original_endpoint ? row.matched_entry : undefined;
}

export interface RowToInputOptions {
  /** Editable durations for ComfyUI video endpoints with available tiers write overrides from durations_text. */
  durationsEditable: boolean;
}

export function rowToInput(r: ModelRow, { durationsEditable }: RowToInputOptions): CustomProviderModelInput {
  let overrides = r.capability_overrides;
  if (durationsEditable) {
    // Raise DurationParseError directly; presave validation already blocks invalid input, so only convert strings honestly
    // into list[int], never silently degrade invalid inputs into blanks, which would replace user tiers with native durations.
    overrides = withDurationsOverride(overrides, parseDurationInput(r.durations_text) ?? undefined);
  }
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
    // Echo other overrides unchanged, including migrated durations/max_output_tokens; full-table PUT omissions clear values.
    capability_overrides: overrides,
    path_overrides: r.path_overrides,
    // Presave validation rejects invalid multipliers; fallback values here never take effect.
    multiplier: parseMultiplier(r.multiplier_text) ?? 1,
    entry_binding: r.entry_binding,
  };
}

/**
 * Protocol compatibility is continuously derived, never a one-time row rewrite on switches: ComfyUI endpoints pair only with
 * ComfyUI providers and vice versa (docs/adr/0081). This requires endpoint catalogs, fetched asynchronously and potentially absent
 * during switches; without derivation nobody reevaluates on arrival. Incompatible old endpoints hide in protocol-filtered
 * selectors, invisible to users until save-time 422.
 *
 * Reassign to first ComfyUI endpoints when entering, new-row default endpoints when leaving. Without ComfyUI endpoints,
 * use UNSET_ENDPOINT: visible and save-blocking rather than invisible old endpoints.
 * Derivation also restores previously selected endpoints after switching away/back: models always store users' last
 * explicit choices. Before catalogs arrive (`catalog` null), no row can be classified; return unchanged.
 */
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
    // Route changes invalidate defaults, capabilities and request-address overrides: validity binds to (endpoint, model_id),
    // and ComfyUI disables overrides entirely (server _check_protocol_constraints), so retaining them guarantees rejection.
    return { ...row, endpoint, is_default: false, capability_overrides: null, path_overrides: null };
  });
}
