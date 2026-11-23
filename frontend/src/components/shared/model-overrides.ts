import type { ModelConfigValue } from "./ModelConfigSection";

export type ModelChannel = "video" | "image" | "text";

type ChannelFields = Pick<
  ModelConfigValue,
  | "videoBackend"
  | "videoProviderI2V"
  | "videoProviderR2V"
  | "imageBackendDefault"
  | "imageBackendT2I"
  | "imageBackendI2I"
  | "textBackendDefault"
  | "textBackendSimple"
  | "textBackendComplex"
>;

export const CHANNEL_MODEL_FIELDS: Record<ModelChannel, readonly (keyof ChannelFields)[]> = {
  video: ["videoBackend", "videoProviderI2V", "videoProviderR2V"],
  image: ["imageBackendDefault", "imageBackendT2I", "imageBackendI2I"],
  text: ["textBackendDefault", "textBackendSimple", "textBackendComplex"],
};

export function countModelOverrides(value: ChannelFields, generateAudio: boolean | null): number {
  const fields = Object.values(CHANNEL_MODEL_FIELDS).flat();
  return fields.filter((field) => value[field] !== "").length + (generateAudio === null ? 0 : 1);
}

export function channelOverridden(channel: ModelChannel, value: ChannelFields, generateAudio: boolean | null): boolean {
  if (channel === "video" && generateAudio !== null) return true;
  return CHANNEL_MODEL_FIELDS[channel].some((field) => value[field] !== "");
}
