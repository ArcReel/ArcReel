import { translate } from "@docusaurus/Translate";

export function getSiteTitle(): string {
  return translate({
    id: "site.title",
    message: "ArcReel 文档中心",
    description: "The documentation site title used in browser tabs and social cards",
  });
}

export function getSiteTagline(): string {
  return translate({
    id: "site.tagline",
    message: "开源、自托管的 AI 视频创作平台",
    description: "The documentation site description used in social cards",
  });
}
