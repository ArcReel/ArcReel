/** Default model + Specify models by use layer calculations (docs/adr/0054); `LayeredModelFields` callers supply per-layer keys. */

import type { AutoBackendsResponse } from "@/types/system";

/**
 * Layer resolution (docs/adr/0054): first nonempty layer wins. All-empty layers require automatic inference, needing
 * provider readiness/registry order unavailable to frontends; return undefined. Backends preview actual selections
 * through `AutoBackendsResponse`; callers querying effective models append them as final layers.
 */
export function effectiveModel(...layers: (string | null | undefined)[]): string | undefined {
  return layers.find((layer) => !!layer) || undefined;
}

/** Global automatic-selection previews; absent (failed/not fetched) means unresolved without blocking remaining calculations. */
interface AutoBackendsLayer {
  auto?: AutoBackendsResponse | null;
}

/**
 * Actual video models under current configurations: reference-to-video uses r2v buckets, other modes i2v, matching backend
 * `VIDEO_BUCKET_BY_GENERATION_MODE`. Overridden buckets differ from default-layer models,
 * so query them for duration/resolution/voice-tier capabilities and model_settings resolution keys.
 * All-empty layers use automatic-selection previews for those buckets.
 */
export function executingVideoModel(
  value: { videoBackend: string; videoProviderI2V: string; videoProviderR2V: string },
  globals: { video: string; videoI2V: string; videoR2V: string } & AutoBackendsLayer,
  usesReferenceImages?: boolean,
): string {
  const bucket = usesReferenceImages ? value.videoProviderR2V : value.videoProviderI2V;
  const globalBucket = usesReferenceImages ? globals.videoR2V : globals.videoI2V;
  const auto = globals.auto?.[usesReferenceImages ? "r2v" : "i2v"];
  return effectiveModel(bucket, value.videoBackend, globalBucket, globals.video, auto) ?? "";
}

/**
 * Actual text-to-image execution models: storyboard/asset images both use this path, storing/reading image resolution by it.
 * Image-to-image has separate execution models; when different, backends miss those keys and use provider defaults. All-empty layers use text-to-image
 * automatic-selection previews.
 */
export function executingImageModel(
  value: { imageBackendDefault: string; imageBackendT2I: string },
  globals: { image: string; imageT2I: string } & AutoBackendsLayer,
): string {
  return (
    effectiveModel(value.imageBackendT2I, value.imageBackendDefault, globals.imageT2I, globals.image, globals.auto?.t2i) ??
    ""
  );
}

/**
 * Subdivision fallback after candidate fetch failures: retain configured rows with only current values as candidates. Saved overrides still
 * apply on backends; hiding entire regions would conceal actual models and prevent clearing overrides. Unconfigured rows lack candidates,
 * remain hidden, and entirely unconfigured subdivisions disappear altogether.
 */
export function degradeSubFieldsToSaved<T extends { value: string; options: string[] }>(
  fields: T[],
  hasCandidates: boolean,
): T[] {
  if (hasCandidates) return fields;
  return fields.filter((f) => !!f.value).map((f) => ({ ...f, options: [f.value] }));
}
