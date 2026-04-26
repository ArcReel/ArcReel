import { ENDPOINT_TO_MEDIA_TYPE, type EndpointKey } from "@/types";

export type DiscoveryFormat = "openai" | "google";
export type ModelLike = { key: string; endpoint: EndpointKey; is_default: boolean };

/** 各 endpoint 在请求 base_url 之后实际拼接的 HTTP 方法与路径片段。
 *  与 lib/custom_provider/endpoints.py 中各 build_backend 对应的真实接口一致：
 *   - openai-images 根据是否带参考图自动分发到 /generations 或 /edits（用 brace 表达两条路径）
 *   - gemini-* 路径中的 ":generateContent" 是动作后缀（按 Google 规范）
 *   - openai-video 主入口 POST /v1/videos（轮询/下载用 GET /v1/videos/:id[/content]） */
export const ENDPOINT_PATHS: Record<EndpointKey, { method: string; path: string }> = {
  "openai-chat": { method: "POST", path: "/v1/chat/completions" },
  "gemini-generate": { method: "POST", path: "/v1beta/models/{model}:generateContent" },
  "openai-images": { method: "POST", path: "/v1/images/{generations,edits}" },
  "gemini-image": { method: "POST", path: "/v1beta/models/{model}:generateContent" },
  "openai-video": { method: "POST", path: "/v1/videos" },
  "newapi-video": { method: "POST", path: "/v1/video/generations" },
};

/** 价格行标签（按 endpoint 推算 media_type）。 */
export function priceLabel(
  endpoint: EndpointKey,
  t: (key: string) => string,
): { input: string; output: string } {
  const media = ENDPOINT_TO_MEDIA_TYPE[endpoint];
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

/** 切 default：仅同 media_type 内互斥；本行 toggle。 */
export function toggleDefaultReducer<T extends ModelLike>(rows: T[], targetKey: string): T[] {
  const target = rows.find((r) => r.key === targetKey);
  if (!target) return rows;
  const targetMedia = ENDPOINT_TO_MEDIA_TYPE[target.endpoint];
  return rows.map((r) => {
    if (ENDPOINT_TO_MEDIA_TYPE[r.endpoint] !== targetMedia) return r;
    if (r.key === targetKey) return { ...r, is_default: !r.is_default };
    return { ...r, is_default: false };
  });
}
