import type { TFunction } from "i18next";
import { formatDurationsLabel } from "@/utils/duration_format";
import { catalogDurations, lookupCatalogVideoAudio, lookupResolutions, lookupVideoAudioControl } from "@/utils/provider-models";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { MediaType, ProviderInfo, VideoRoute } from "@/types";

/**
 * Dropdown-row capability renderers (duration/resolution/audio), passed to `ProviderModelSelect.renderOptionMeta`.
 * Project/global settings share assembly, differing only in whether `endpointToMediaType` exists, without duplicate implementations.
 *
 * Deliberately not hooks: callers may construct renderers after early-return branches, so internal `useTranslation` would violate
 * hook ordering. Callers pass `t`; all keys have `dashboard:` prefixes to avoid caller default namespaces.
 */
export function videoOptionMetaRenderer({
  t,
  providers,
  customProviders,
  endpointToMediaType,
  defaultRoute,
}: {
  t: TFunction;
  providers: ProviderInfo[];
  customProviders: CustomProviderInfo[];
  endpointToMediaType?: Record<string, MediaType>;
  /** Default-layer dropdown execution paths. Default layers span all uses; only caller contexts provide paths (whether projects use reference-to-
   *  video). Global settings lack project contexts; omission falls back to catalog i2v positions. Subdivision dropdowns ignore this value: their
   *  `key` is already the path; see `asVideoRoute` below. */
  defaultRoute?: VideoRoute;
}) {
  return (fullValue: string, subFieldKey?: string) => {
    // Subdivision dropdowns own paths; only defaults fall back to caller paths. Three simultaneous video selects follow different paths;
    // sharing one would label image-to-video cells with reference-to-video criteria, or vice versa.
    const route = asVideoRoute(subFieldKey) ?? defaultRoute;
    const durations = catalogDurations(providers, customProviders, fullValue);
    const resolutions = lookupResolutions(
      providers,
      fullValue,
      customProviders,
      endpointToMediaType,
    ).options;
    // Unresolved models yield null and omit entire audio cells; neither branch invents answers.
    const audioControl = route ? lookupVideoAudioControl(providers, fullValue, route) : null;
    const hasAudioTrack = route
      ? (audioControl === null ? null : audioControl !== "always_off")
      : (lookupCatalogVideoAudio(providers, fullValue)?.hasAudioTrack ?? null);
    const parts: string[] = [];
    if (durations?.length) parts.push(formatDurationsLabel(durations));
    if (resolutions.length) parts.push(resolutions.join(" / "));
    if (hasAudioTrack !== null) {
      parts.push(t(hasAudioTrack ? "dashboard:video_spec_audio_has" : "dashboard:video_spec_audio_none"));
    }
    return parts.length > 0 ? parts.join(" · ") : t("dashboard:video_option_caps_unknown");
  };
}

/**
 * Video execution paths from subdivision `key` values. `LayeredModelFields` subdivisions are named after task-type buckets; two video buckets
 * are exactly the two paths. Image buckets (t2i/i2i) and text tiers are not video paths; return null for default-layer path fallbacks.
 */
function asVideoRoute(subFieldKey: string | undefined): VideoRoute | null {
  return subFieldKey === "i2v" || subFieldKey === "r2v" ? subFieldKey : null;
}
