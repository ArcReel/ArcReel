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


export async function getProviderModels(options: { signal?: AbortSignal } = {}): Promise<ProviderInfo[]> {
  const res = await API.getProviders(options);
  return res.providers;
}

export async function getCustomProviderModels(options: { signal?: AbortSignal } = {}): Promise<CustomProviderInfo[]> {
  const res = await API.listCustomProviders(options);
  return res.providers;
}


export interface CatalogDisplayNames {
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

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

export function lookupSupportedDurations(
  providers: ProviderInfo[],
  videoBackend: string,
  customProviders?: CustomProviderInfo[],
): number[] | undefined {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return undefined;
  const providerId = videoBackend.slice(0, slashIdx);
  const modelId = videoBackend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX) && customProviders) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const cp = customProviders.find((p) => p.id === dbId);
    const model = cp?.models?.find((m) => m.model_id === modelId);
    const durations = model?.capability_overrides?.durations;
    return durations?.length ? durations : undefined;
  }

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return model?.supported_durations?.length
    ? model.supported_durations
    : undefined;
}

export function catalogDurations(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[],
  videoBackend: string,
): number[] | null {
  if (!videoBackend) return null;
  const raw = lookupSupportedDurations(providers, videoBackend, customProviders);
  return raw?.length ? [...raw].sort((a, b) => a - b) : null;
}

export interface CatalogVideoAudio {
  hasAudioTrack: boolean;
  voiceConsistency: VoiceConsistencyTier;
}

export function lookupCatalogVideoAudio(
  providers: ProviderInfo[],
  videoBackend: string,
  customProviders: CustomProviderInfo[] = [],
): CatalogVideoAudio | null {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return null;
  const providerId = videoBackend.slice(0, slashIdx);
  const modelId = videoBackend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX)) {
    const model = customProviders.find((p) => `${CUSTOM_PREFIX}${p.id}` === providerId)?.models.find((m) => m.model_id === modelId);
    if (!model?.audio_track || !model.voice_consistency) return null;
    return { hasAudioTrack: model.audio_track !== "always_off", voiceConsistency: model.voice_consistency };
  }

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  if (!model) return null;
  return { hasAudioTrack: model.audio_track !== "always_off", voiceConsistency: model.voice_consistency };
}

export function lookupVideoAudioControl(
  providers: ProviderInfo[],
  videoBackend: string,
  route: VideoRoute,
  customProviders: CustomProviderInfo[] = [],
): VideoAudioControl | null {
  const slashIdx = videoBackend.indexOf("/");
  if (slashIdx === -1) return null;
  const providerId = videoBackend.slice(0, slashIdx);
  if (providerId.startsWith(CUSTOM_PREFIX)) {
    const model = customProviders.find((p) => `${CUSTOM_PREFIX}${p.id}` === providerId)?.models.find((m) => m.model_id === videoBackend.slice(slashIdx + 1));
    return (route === "r2v" ? model?.reference_route_audio_track : model?.audio_track) ?? null;
  }

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[videoBackend.slice(slashIdx + 1)];
  if (!model) return null;
  return route === "r2v" ? model.reference_route_audio_track : model.audio_track;
}


export const IMAGE_STANDARD_RESOLUTIONS = ["1K", "2K", "4K"];
export const VIDEO_STANDARD_RESOLUTIONS = ["480p", "720p", "1080p", "4K"];

export function lookupResolutions(
  providers: ProviderInfo[],
  backend: string,
  customProviders?: CustomProviderInfo[],
  endpointToMediaType?: Record<string, MediaType>,
): { options: string[]; isCustom: boolean; isKnown: boolean } {
  const slashIdx = backend.indexOf("/");
  if (slashIdx === -1) return { options: [], isCustom: false, isKnown: false };
  const providerId = backend.slice(0, slashIdx);
  const modelId = backend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX) && customProviders) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const cp = customProviders.find((p) => p.id === dbId);
    const model = cp?.models?.find((m) => m.model_id === modelId);
    if (!model) return { options: [], isCustom: true, isKnown: false };
    const media = endpointToMediaType?.[model.endpoint];
    const standard =
      media === "image"
        ? IMAGE_STANDARD_RESOLUTIONS
        : media === "video"
          ? VIDEO_STANDARD_RESOLUTIONS
          : [];
    return { options: standard, isCustom: true, isKnown: true };
  }

  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return { options: model?.resolutions ?? [], isCustom: false, isKnown: !!model };
}

export function lookupQualities(
  providers: ProviderInfo[],
  backend: string,
  customProviders?: CustomProviderInfo[],
): string[] {
  const slashIdx = backend.indexOf("/");
  if (slashIdx === -1) return [];
  const providerId = backend.slice(0, slashIdx);
  const modelId = backend.slice(slashIdx + 1);

  if (providerId.startsWith(CUSTOM_PREFIX)) {
    const dbId = parseInt(providerId.slice(CUSTOM_PREFIX.length), 10);
    const model = customProviders?.find((p) => p.id === dbId)?.models?.find((m) => m.model_id === modelId);
    return model?.qualities ?? [];
  }

  return providers.find((p) => p.id === providerId)?.models?.[modelId]?.qualities ?? [];
}

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

export function resolutionPlaceholder(
  constraints: EndpointConstraints | undefined,
  t: (key: string, params?: Record<string, unknown>) => string,
): string {
  const native = constraints?.nativeResolution;
  if (native) return t("resolution_native_placeholder", { value: native });
  if (constraints?.sizeFixed) return t("resolution_endpoint_fixed_placeholder");
  return t("resolution_default_placeholder");
}
