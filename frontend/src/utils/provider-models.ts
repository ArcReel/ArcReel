import { API } from "@/api";
import type { CustomProviderInfo, MediaType, ProviderInfo } from "@/types";

export const DEFAULT_DURATIONS: readonly number[] = [4, 6, 8];

const CUSTOM_PREFIX = "custom-";

// ---------------------------------------------------------------------------
// Built-in providers cache
// ---------------------------------------------------------------------------

let _cache: ProviderInfo[] | null = null;
let _promise: Promise<ProviderInfo[]> | null = null;

/** Fetch (or return cached) built-in provider list including models. */
export async function getProviderModels(): Promise<ProviderInfo[]> {
  if (_cache) return _cache;
  if (!_promise) {
    _promise = API.getProviders()
      .then((res) => {
        _cache = res.providers;
        _promise = null;
        return _cache;
      })
      .catch((err) => {
        _promise = null;
        throw err;
      });
  }
  return _promise;
}

// ---------------------------------------------------------------------------
// Custom providers cache
// ---------------------------------------------------------------------------

let _customCache: CustomProviderInfo[] | null = null;
let _customPromise: Promise<CustomProviderInfo[]> | null = null;

/** Fetch (or return cached) custom provider list. */
export async function getCustomProviderModels(): Promise<CustomProviderInfo[]> {
  if (_customCache) return _customCache;
  if (!_customPromise) {
    _customPromise = API.listCustomProviders()
      .then((res) => {
        _customCache = res.providers;
        _customPromise = null;
        return _customCache;
      })
      .catch((err) => {
        _customPromise = null;
        throw err;
      });
  }
  return _customPromise;
}

// ---------------------------------------------------------------------------
// Cache invalidation
// ---------------------------------------------------------------------------

/** Invalidate all provider caches (call after provider config changes). */
export function invalidateProviderModelsCache(): void {
  _cache = null;
  _promise = null;
  _customCache = null;
  _customPromise = null;
}

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

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
    if (model?.supported_durations?.length) {
      return model.supported_durations;
    }
    return undefined;
  }

  // Built-in provider
  const provider = providers.find((p) => p.id === providerId);
  const model = provider?.models?.[modelId];
  return model?.supported_durations?.length
    ? model.supported_durations
    : undefined;
}

// ---------------------------------------------------------------------------
// Resolution lookup
// ---------------------------------------------------------------------------

export const IMAGE_STANDARD_RESOLUTIONS = ["512px", "1K", "2K", "4K"];
export const VIDEO_STANDARD_RESOLUTIONS = ["480p", "720p", "1080p", "4K"];

/** 返回该 (provider, model) 下的分辨率候选 + 是否自定义供应商（决定 picker 模式）。
 *  自定义 provider 路径需要从 endpoint 推 media_type 选标准分辨率集；该 map 由调用方
 *  从 endpoint-catalog-store 读出注入（保持本文件无 store 副作用）。 */
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
