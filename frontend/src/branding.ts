
const env = import.meta.env as Record<string, string | undefined>;

const DEFAULT_DOCS_URL = "https://docs.arc-reel.com";

function fallback(value: string | undefined, defaultValue: string): string {
  if (typeof value !== "string") return defaultValue;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : defaultValue;
}

function normalizeBaseUrl(value: string | undefined, defaultValue: string): string {
  const normalized = fallback(value, defaultValue).replace(/\/+$/, "");
  return normalized.length > 0 ? normalized : defaultValue;
}

export const BRAND = {
  name: fallback(env.VITE_BRAND_NAME, "ArcReel"),
  tagline: fallback(env.VITE_BRAND_TAGLINE, "Agent 驱动的 AI 视频创作工作台"),
  description: fallback(
    env.VITE_BRAND_DESCRIPTION,
    "AI 视频创作工作台，统一管理项目、脚本、分镜、视频生成与 Agent 对话。",
  ),
  docsUrl: normalizeBaseUrl(env.VITE_BRAND_DOCS_URL, DEFAULT_DOCS_URL),
} as const;

export const BRAND_DOCUMENT_TITLE = `${BRAND.name} · ${BRAND.tagline}`;

export function getDocsUrl(language: string | undefined, baseUrl: string = BRAND.docsUrl): string {
  const base = normalizeBaseUrl(baseUrl, DEFAULT_DOCS_URL);
  const normalizedLanguage = (language ?? "").trim().toLowerCase();
  if (
    normalizedLanguage === "zh"
    || normalizedLanguage.startsWith("zh-")
    || normalizedLanguage.startsWith("zh_")
  ) {
    return `${base}/`;
  }
  return `${base}/en/`;
}

