// Brand configuration — single source of truth for product naming.
// Override at build time via Vite env vars
// (VITE_BRAND_NAME / VITE_BRAND_TAGLINE / VITE_BRAND_DESCRIPTION).
//
// Source code references BRAND.name (or the [[brand]] placeholder in i18n
// resources) so the displayed product name is not hardcoded across files.
// Defaults preserve the upstream brand; downstream distributions can override
// via frontend/.env without code changes.

const env = import.meta.env as Record<string, string | undefined>;

function fallback(value: string | undefined, defaultValue: string): string {
  // Trim + empty check so VITE_BRAND_NAME="" (or whitespace) falls back to the
  // default, matching the documented "Empty = upstream defaults" contract.
  if (typeof value !== "string") return defaultValue;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : defaultValue;
}

export const BRAND = {
  name: fallback(env.VITE_BRAND_NAME, "ArcReel"),
  tagline: fallback(env.VITE_BRAND_TAGLINE, "AI 视频创作工作台"),
  description: fallback(
    env.VITE_BRAND_DESCRIPTION,
    "AI 视频创作工作台，统一管理项目、剧本、分镜、视频生成与助手对话。",
  ),
} as const;

export const BRAND_DOCUMENT_TITLE = `${BRAND.name} · ${BRAND.tagline}`;

