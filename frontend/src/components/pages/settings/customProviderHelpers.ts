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
 *  互斥清理仅在 enabling（false→true）时触发——取消已有默认（true→false）时不应连带清掉
 *  其他默认项，否则一次"取消"会误删兄弟槽位（如 wildcard ↔ split-edits 的 I2I 重叠）。
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
  const isEnabling = !target.is_default;
  const targetMedia = endpointToMediaType[target.endpoint];
  if (targetMedia === undefined) {
    return rows.map((r) => (r.key === targetKey ? { ...r, is_default: isEnabling } : r));
  }
  // text / video：仅 enabling 时清同 media_type 其他默认
  if (targetMedia !== "image") {
    return rows.map((r) => {
      if (r.key === targetKey) return { ...r, is_default: isEnabling };
      if (!isEnabling) return r;
      if (endpointToMediaType[r.endpoint] !== targetMedia) return r;
      return { ...r, is_default: false };
    });
  }
  // image：仅 enabling 时按 capability 交集清冲突
  const targetCaps = endpointToImageCaps[target.endpoint] ?? [];
  return rows.map((r) => {
    if (r.key === targetKey) return { ...r, is_default: isEnabling };
    if (!isEnabling) return r;
    if (endpointToMediaType[r.endpoint] !== "image") return r;
    const rowCaps = endpointToImageCaps[r.endpoint] ?? [];
    const overlap = rowCaps.some((c) => targetCaps.includes(c));
    return overlap ? { ...r, is_default: false } : r;
  });
}
