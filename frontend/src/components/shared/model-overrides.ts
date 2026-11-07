import type { ModelConfigValue } from "./ModelConfigSection";

/** Three project model channels overriding global defaults. */
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

/** Model fields that follow global settings when empty: default model and two subdivisions per channel, or three text tiers. */
export const CHANNEL_MODEL_FIELDS: Record<ModelChannel, readonly (keyof ChannelFields)[]> = {
  video: ["videoBackend", "videoProviderI2V", "videoProviderR2V"],
  image: ["imageBackendDefault", "imageBackendT2I", "imageBackendI2I"],
  text: ["textBackendDefault", "textBackendSimple", "textBackendComplex"],
};

/**
 * Project override count: nonempty fields among nine model fields, plus one when Generate video with audio is not following global settings (nonnull).
 * Exclude resolution and default duration: they have no global layer to follow.
 */
export function countModelOverrides(value: ChannelFields, generateAudio: boolean | null): number {
  const fields = Object.values(CHANNEL_MODEL_FIELDS).flat();
  return fields.filter((field) => value[field] !== "").length + (generateAudio === null ? 0 : 1);
}

/** Whether the channel overrides global defaults. Generate video with audio belongs to the video channel. */
export function channelOverridden(channel: ModelChannel, value: ChannelFields, generateAudio: boolean | null): boolean {
  if (channel === "video" && generateAudio !== null) return true;
  return CHANNEL_MODEL_FIELDS[channel].some((field) => value[field] !== "");
}
