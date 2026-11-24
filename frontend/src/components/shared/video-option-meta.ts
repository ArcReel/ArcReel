import type { TFunction } from "i18next";
import { formatDurationsLabel } from "@/utils/duration_format";
import { catalogDurations, lookupCatalogVideoAudio, lookupResolutions, lookupVideoAudioControl } from "@/utils/provider-models";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { MediaType, ProviderInfo, VideoRoute } from "@/types";

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
  defaultRoute?: VideoRoute;
}) {
  return (fullValue: string, subFieldKey?: string) => {
    const route = asVideoRoute(subFieldKey) ?? defaultRoute;
    const durations = catalogDurations(providers, customProviders, fullValue);
    const resolutions = lookupResolutions(
      providers,
      fullValue,
      customProviders,
      endpointToMediaType,
    ).options;
    const audioControl = route ? lookupVideoAudioControl(providers, fullValue, route, customProviders) : null;
    const hasAudioTrack = route
      ? (audioControl === null ? null : audioControl !== "always_off")
      : (lookupCatalogVideoAudio(providers, fullValue, customProviders)?.hasAudioTrack ?? null);
    const parts: string[] = [];
    if (durations?.length) parts.push(formatDurationsLabel(durations));
    if (resolutions.length) parts.push(resolutions.join(" / "));
    if (hasAudioTrack !== null) {
      parts.push(t(hasAudioTrack ? "dashboard:video_spec_audio_has" : "dashboard:video_spec_audio_none"));
    }
    return parts.length > 0 ? parts.join(" · ") : t("dashboard:video_option_caps_unknown");
  };
}

function asVideoRoute(subFieldKey: string | undefined): VideoRoute | null {
  return subFieldKey === "i2v" || subFieldKey === "r2v" ? subFieldKey : null;
}
