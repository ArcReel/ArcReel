import type { EndpointKey, ImageCap, MediaType } from "@/types";

export type DiscoveryFormat = "openai" | "google";
export type ModelLike = { key: string; endpoint: EndpointKey; is_default: boolean };

/** 价格行标签 —— mediaType 由调用方从 endpoint-catalog-store 读出注入。 */
export function priceLabel(
  endpoint: EndpointKey,
  endpointToMediaType: Record<string, MediaType>,
  t: (key: string) => string,
): { input: string; output: string } {
  const media = endpointToMediaType[endpoint];
  if (media === "video") return { input: t("price_per_second"), output: "" };
  if (media === "image") return { input: t("price_per_image"), output: "" };
  return { input: t("price_per_m_input"), output: t("price_per_m_output") };
}

/** /models URL 预览。 */
export function urlPreviewFor(format: DiscoveryFormat, rawBaseUrl: string): string | null {
  const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  if (format === "openai") {
    const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
    return `${base}/models`;
  }
  const base = trimmed.replace(/\/v\d+\w*$/, "");
  return `${base}/v1beta/models`;
}

/** 切 default：text/video 同 media_type 内互斥；image 按 capability 集合交集互斥。
 *  catalog 未加载或 endpoint 不在映射内时降级为「单行 toggle」——避免所有 endpoint
 *  都解析成 undefined 时被当作同组，误清掉其他媒体类型的默认项。
 *
 *  endpointToImageCaps：来自 endpoint-catalog-store，仅 image endpoint 有条目。 */
export function toggleDefaultReducer<T extends ModelLike>(
  rows: T[],
  targetKey: string,
  endpointToMediaType: Record<string, MediaType>,
  endpointToImageCaps: Record<string, ImageCap[] | undefined> = {},
): T[] {
  const target = rows.find((r) => r.key === targetKey);
  if (!target) return rows;
  const targetMedia = endpointToMediaType[target.endpoint];
  if (targetMedia === undefined) {
    return rows.map((r) => (r.key === targetKey ? { ...r, is_default: !r.is_default } : r));
  }
  // text / video：保留旧规则——同 media_type 内互斥
  if (targetMedia !== "image") {
    return rows.map((r) => {
      if (endpointToMediaType[r.endpoint] !== targetMedia) return r;
      if (r.key === targetKey) return { ...r, is_default: !r.is_default };
      return { ...r, is_default: false };
    });
  }
  // image：按 capability 交集互斥（同一 capability 槽至多 1 个默认）
  const targetCaps = endpointToImageCaps[target.endpoint] ?? [];
  return rows.map((r) => {
    if (r.key === targetKey) return { ...r, is_default: !r.is_default };
    if (endpointToMediaType[r.endpoint] !== "image") return r;
    const rowCaps = endpointToImageCaps[r.endpoint] ?? [];
    const overlap = rowCaps.some((c) => targetCaps.includes(c));
    return overlap ? { ...r, is_default: false } : r;
  });
}
