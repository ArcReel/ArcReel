
export const PRICE_CURRENCIES = ["USD", "CNY"] as const;
export const PRICE_SERVICE_TIERS = ["standard", "flex", "fast"] as const;
export const CJK_DOUBLE_METER = "characters";

export const METERS = {
  uncached_input_tokens: { kind: "token", per: 1000000, mediaTypes: ["text", "image", "audio"] },
  cache_read_tokens: { kind: "token", per: 1000000, mediaTypes: ["text", "image"] },
  cache_write_5m_tokens: { kind: "token", per: 1000000, mediaTypes: ["text"] },
  cache_write_1h_tokens: { kind: "token", per: 1000000, mediaTypes: ["text"] },
  output_tokens: { kind: "token", per: 1000000, mediaTypes: ["text", "image"] },
  audio_input_tokens: { kind: "token", per: 1000000, mediaTypes: ["text"] },
  output_images: { kind: "count", per: 1, mediaTypes: ["image"] },
  input_images: { kind: "count", per: 1, mediaTypes: ["image", "video"] },
  uncached_image_input_tokens: { kind: "token", per: 1000000, mediaTypes: ["image"] },
  image_cache_read_tokens: { kind: "token", per: 1000000, mediaTypes: ["image"] },
  image_output_tokens: { kind: "token", per: 1000000, mediaTypes: ["image"] },
  output_seconds: { kind: "count", per: 1, mediaTypes: ["video"] },
  video_tokens: { kind: "count", per: 1000000, mediaTypes: ["video"] },
  input_video_seconds: { kind: "count", per: 1, mediaTypes: ["video"] },
  characters: { kind: "count", per: 10000, mediaTypes: ["audio"] },
  audio_output_tokens: { kind: "token", per: 1000000, mediaTypes: ["audio"] },
  requests: { kind: "count", per: 1, mediaTypes: ["text", "image", "video", "audio"] },
} as const;

export const DIMENSIONS = {
  context_tier: { kind: "tier", mediaTypes: ["text", "image", "audio"] },
  resolution: { kind: "label", mediaTypes: ["image", "video"] },
  quality: { kind: "label", mediaTypes: ["image"] },
  audio: { kind: "flag", mediaTypes: ["video"] },
  service_tier: { kind: "service_tier", mediaTypes: ["text", "image", "video"] },
  duration: { kind: "seconds", mediaTypes: ["video"] },
  input_video: { kind: "flag", mediaTypes: ["video"] },
  reference_to_video: { kind: "flag", mediaTypes: ["video"] },
} as const;
