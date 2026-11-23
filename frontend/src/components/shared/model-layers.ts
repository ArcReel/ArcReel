
import type { AutoBackendsResponse } from "@/types/system";

export function effectiveModel(...layers: (string | null | undefined)[]): string | undefined {
  return layers.find((layer) => !!layer) || undefined;
}

interface AutoBackendsLayer {
  auto?: AutoBackendsResponse | null;
}

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

export function executingImageModel(
  value: { imageBackendDefault: string; imageBackendT2I: string; imageBackendI2I?: string },
  globals: { image: string; imageT2I: string; imageI2I?: string } & AutoBackendsLayer,
  purpose: "t2i" | "i2i" = "t2i",
): string {
  const bucket = purpose === "i2i" ? value.imageBackendI2I : value.imageBackendT2I;
  const globalBucket = purpose === "i2i" ? globals.imageI2I : globals.imageT2I;
  const auto = globals.auto?.[purpose];
  return effectiveModel(bucket, value.imageBackendDefault, globalBucket, globals.image, auto) ?? "";
}

export function degradeSubFieldsToSaved<T extends { value: string; options: string[] }>(
  fields: T[],
  hasCandidates: boolean,
): T[] {
  if (hasCandidates) return fields;
  return fields.filter((f) => !!f.value).map((f) => ({ ...f, options: [f.value] }));
}
