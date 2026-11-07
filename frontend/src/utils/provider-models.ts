import { API } from "@/api";
import type { EndpointConstraints } from "@/stores/endpoint-catalog-store";
import type {
  CustomProviderInfo,
  MediaType,
  ProviderInfo,
  VideoAudioControl,
  VideoRoute,
  VoiceConsistencyTier,
} from "@/types";

const CUSTOM_PREFIX = "custom-";

// ---------------------------------------------------------------------------
// Provider fetchers
//
// Provider configuration changes (supported_durations/enablement/etc.), so frontend never persistently caches it:
// fetch backends per consumption, avoiding long-lived copies drifting from backend truth (ADR 0035, frontend implications of ADR 0018/0013).
// ---------------------------------------------------------------------------

/** Fetch the built-in provider list (including models) fresh on every call. */
export async function getProviderModels(options: { signal?: AbortSignal } = {}): Promise<ProviderInfo[]> {
  const res = await API.getProviders(options);
  return res.providers;
}

/** Fetch the custom provider list fresh on every call. */
export async function getCustomProviderModels(options: { signal?: AbortSignal } = {}): Promise<CustomProviderInfo[]> {
  const res = await API.listCustomProviders(options);
  return res.providers;
}

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

/** Catalog provider/model names keyed by "provider/model", already rendered through Accept-Language. */
export interface CatalogDisplayNames {
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

/**
 * Extract display-name maps from catalogs (`/providers` / `/custom-providers`) as fallback beneath candidates.
 *
 * Neither endpoint narrows availability, unlike candidate/config lists limited to ready built-ins/enabled custom models;
 * effective values may still reference built-ins with lost credentials or disabled custom models (disabling preserves references).
 * Dropdown triggers must still show names, never bare IDs such as `custom-3 · my-model`.
 */
export function catalogDisplayNames(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[] = [],
): CatalogDisplayNames {
  const providerNames: Record<string, string> = {};
  const modelNames: Record<string, string> = {};
  for (const provider of providers) {
    providerNames[provider.id] = provider.display_name;
    for (const [modelId, model] of Object.entries(provider.models ?? {})) {
      modelNames[`${provider.id}/${modelId}`] = model.display_name;
    }
  }
  for (const provider of customProviders) {
    const providerId = `${CUSTOM_PREFIX}${provider.id}`;
    providerNames[providerId] = provider.display_name;
    for (const model of provider.models ?? []) {
      modelNames[`${providerId}/${model.model_id}`] = model.display_name;
    }
  }
  return { providerNames, modelNames };
}

/**
 * Given a video backend string like "gemini-aistudio/veo-3.1-generate-preview"
 * or "custom-3/my-model", look up supported_durations.
 * Returns undefined if provider/model not found.
 */
export function lookupSupportedDurations(
  providers: ProviderInfo[],
  videoBackend: string,
  customProviders?: CustomProviderInfo[],
): number[] | undefined {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return undefined;
  const providerId = videoBackend.slice(0, slashIdx);
  const modelId = videoBackend.slice(slashIdx + 1);

  // Custom provider: "custom-{db_id}/{model_id}"
  if (providerId.startsWith(CUSTOM_PREFIX) && customProviders) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const cp = customProviders.find((p) => p.id === dbId);
    const model = cp?.models?.find((m) => m.model_id === modelId);
    // Custom-model durations come from corresponding catalog entries; frontends see only user-entered overrides;
    // leave other cases to server capability queries (useModelCapabilities).
    const durations = model?.capability_overrides?.durations;
    return durations?.length ? durations : undefined;
  }

  // Built-in provider
  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return model?.supported_durations?.length
    ? model.supported_durations
    : undefined;
}

/**
 * Complete ascending model-duration sets from catalogs; null when unresolved.
 *
 * Declared sets without contextual narrowing, for synchronous model-spec displays and checking selected durations
 * against newly switched models. Current resolution/reference-path narrowed options come only from server
 * video-capabilities endpoints (`useModelCapabilities`); frontends never duplicate narrowing rules.
 */
export function catalogDurations(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[],
  videoBackend: string,
): number[] | null {
  if (!videoBackend) return null;
  const raw = lookupSupportedDurations(providers, videoBackend, customProviders);
  return raw?.length ? [...raw].sort((a, b) => a - b) : null;
}

/** Catalog video audio capabilities (noncustom providers): track presence plus server-derived voice-consistency tiers. */
export interface CatalogVideoAudio {
  hasAudioTrack: boolean;
  /** Server-derived tiers without project contexts; with projects, query capabilities instead of reading this value. */
  voiceConsistency: VoiceConsistencyTier;
}

/**
 * Look up audio declarations by "provider/model", shared by dropdown audio capabilities/global tier
 * badges. Custom catalogs lack per-model declarations; match server `_resolve_video_caps_for_model`
 * by assuming audio (soft). Without signals, audio with degraded guarantees is less misleading than falsely assuming silence.
 */
export function lookupCatalogVideoAudio(
  providers: ProviderInfo[],
  videoBackend: string,
): CatalogVideoAudio | null {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return null;
  const providerId = videoBackend.slice(0, slashIdx);
  const modelId = videoBackend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX)) return { hasAudioTrack: true, voiceConsistency: "soft" };

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  if (!model) return null;
  return { hasAudioTrack: model.audio_track !== "always_off", voiceConsistency: model.voice_consistency };
}

/**
 * Audio-toggle controllability for `provider/model` on execution paths; unresolved models yield null (callers assume controllable,
 * without tightening).
 *
 * Servers derive tristates from backend VideoCapabilities; frontends select path fields without interpreting
 * capability tokens or synthesizing states. Always-audio/always-silent outputs ignore toggles, so settings disable
 * them; otherwise Off intentions cannot reach providers yet orchestrators would strip all timbre constraints as silent.
 *
 * `route` is required: the same model may differ by subpath (Kling v3-omni controls image-to-video but lacks reference-to-video
 * toggles); context-free values could let users enable audio while receiving silent reference outputs.
 *
 * Custom-provider catalogs lack per-model audio declarations; match server `_resolve_video_caps_for_model`
 * by never tightening without signals, leaving toggles controllable.
 */
export function lookupVideoAudioControl(
  providers: ProviderInfo[],
  videoBackend: string,
  route: VideoRoute,
): VideoAudioControl | null {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return null;
  const providerId = videoBackend.slice(0, slashIdx);
  if (providerId.startsWith(CUSTOM_PREFIX)) return "controllable";

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[videoBackend.slice(slashIdx + 1)];
  if (!model) return null;
  return route === "r2v" ? model.reference_route_audio_track : model.audio_track;
}

// ---------------------------------------------------------------------------
// Resolution lookup
// ---------------------------------------------------------------------------

export const IMAGE_STANDARD_RESOLUTIONS = ["512px", "1K", "2K", "4K"];
export const VIDEO_STANDARD_RESOLUTIONS = ["480p", "720p", "1080p", "4K"];

/** Return (provider, model) resolution candidates/custom-provider status, determining picker modes.
 *  Custom paths infer media_type from endpoints to select standard resolution sets; callers inject this map
 *  from endpoint-catalog-store, keeping this file free of store side effects. */
export function lookupResolutions(
  providers: ProviderInfo[],
  backend: string,
  customProviders?: CustomProviderInfo[],
  endpointToMediaType?: Record<string, MediaType>,
): { options: string[]; isCustom: boolean } {
  const slashIdx = backend.indexOf("/");
  if (slashIdx === -1) return { options: [], isCustom: false };
  const providerId = backend.slice(0, slashIdx);
  const modelId = backend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX) && customProviders) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const cp = customProviders.find((p) => p.id === dbId);
    const model = cp?.models?.find((m) => m.model_id === modelId);
    if (!model) return { options: [], isCustom: true };
    const media = endpointToMediaType?.[model.endpoint];
    const standard =
      media === "image"
        ? IMAGE_STANDARD_RESOLUTIONS
        : media === "video"
          ? VIDEO_STANDARD_RESOLUTIONS
          : [];
    return { options: standard, isCustom: true };
  }

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return { options: model?.resolutions ?? [], isCustom: false };
}

/** Size/duration constraints from endpoints associated with `provider/model`; undefined for built-ins/unresolved models.
 *
 *  Built-ins have no endpoint-fixed dimensions: sizes/durations are request parameters, with no corresponding
 *  endpoint-catalog constraints. Look up custom-provider model rows only.
 */
export function lookupEndpointConstraints(
  backend: string,
  customProviders: CustomProviderInfo[] | undefined,
  endpointConstraints: Record<string, EndpointConstraints>,
): EndpointConstraints | undefined {
  const slashIdx = backend.indexOf("/");
  if (slashIdx === -1) return undefined;
  const providerId = backend.slice(0, slashIdx);
  if (!providerId.startsWith(CUSTOM_PREFIX) || !customProviders) return undefined;
  const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
  const model = customProviders
    .find((p) => p.id === dbId)
    ?.models?.find((m) => m.model_id === backend.slice(slashIdx + 1));
  return model ? endpointConstraints[model.endpoint] : undefined;
}

/** Resolution empty placeholders explain outcomes of no selection.
 *
 *  Three forms: report native dimensions when available (literal ComfyUI width/height bindings); when endpoints fix dimensions
 *  without readable literals (most commonly both unbound width/height), say Determined by workflow.
 *  Selectors are already disabled, and generic Default (omit) would imply selectable omission choices; other
 *  endpoints omit parameters on no selection, accurately described by Default (omit).
 */
export function resolutionPlaceholder(
  constraints: EndpointConstraints | undefined,
  t: (key: string, params?: Record<string, unknown>) => string,
): string {
  const native = constraints?.nativeResolution;
  if (native) return t("resolution_native_placeholder", { value: native });
  if (constraints?.sizeFixed) return t("resolution_endpoint_fixed_placeholder");
  return t("resolution_default_placeholder");
}
