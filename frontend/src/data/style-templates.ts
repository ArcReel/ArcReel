/** 风格模版前端清单（id + category + thumbnail，prompt 由后端展开）。 */
export type StyleCategory = "live" | "anim";

export interface StyleTemplate {
  id: string;
  category: StyleCategory;
  thumbnail: string;  // 静态资源 URL（public/style-thumbnails/）
}

export const STYLE_TEMPLATES: StyleTemplate[] = [
  // ===== 真人 (18) =====
  { id: "live_cinematic_ancient", category: "live", thumbnail: "/style-thumbnails/live_cinematic_ancient.png" },
  { id: "live_zhang_yimou",       category: "live", thumbnail: "/style-thumbnails/live_zhang_yimou.png" },
  { id: "live_ancient_xianxia",   category: "live", thumbnail: "/style-thumbnails/live_ancient_xianxia.png" },
  { id: "live_premium_drama",     category: "live", thumbnail: "/style-thumbnails/live_premium_drama.png" },
  { id: "live_cinema",            category: "live", thumbnail: "/style-thumbnails/live_cinema.png" },
  { id: "live_spartan",           category: "live", thumbnail: "/style-thumbnails/live_spartan.png" },
  { id: "live_bladerunner",       category: "live", thumbnail: "/style-thumbnails/live_bladerunner.png" },
  { id: "live_got",               category: "live", thumbnail: "/style-thumbnails/live_got.png" },
  { id: "live_breaking_bad",      category: "live", thumbnail: "/style-thumbnails/live_breaking_bad.png" },
  { id: "live_kdrama",            category: "live", thumbnail: "/style-thumbnails/live_kdrama.png" },
  { id: "live_kurosawa",          category: "live", thumbnail: "/style-thumbnails/live_kurosawa.png" },
  { id: "live_nolan",             category: "live", thumbnail: "/style-thumbnails/live_nolan.png" },
  { id: "live_tarantino",         category: "live", thumbnail: "/style-thumbnails/live_tarantino.png" },
  { id: "live_lynch",             category: "live", thumbnail: "/style-thumbnails/live_lynch.png" },
  { id: "live_anderson",          category: "live", thumbnail: "/style-thumbnails/live_anderson.png" },
  { id: "live_wong",              category: "live", thumbnail: "/style-thumbnails/live_wong.png" },
  { id: "live_shaw",              category: "live", thumbnail: "/style-thumbnails/live_shaw.png" },
  { id: "live_cyberpunk",         category: "live", thumbnail: "/style-thumbnails/live_cyberpunk.png" },
  // ===== 动画 (18) =====
  { id: "anim_3d_cg",             category: "anim", thumbnail: "/style-thumbnails/anim_3d_cg.png" },
  { id: "anim_cn_3d",             category: "anim", thumbnail: "/style-thumbnails/anim_cn_3d.png" },
  { id: "anim_kyoto",             category: "anim", thumbnail: "/style-thumbnails/anim_kyoto.png" },
  { id: "anim_arcane",            category: "anim", thumbnail: "/style-thumbnails/anim_arcane.png" },
  { id: "anim_us_3d",             category: "anim", thumbnail: "/style-thumbnails/anim_us_3d.png" },
  { id: "anim_ink_wushan",        category: "anim", thumbnail: "/style-thumbnails/anim_ink_wushan.png" },
  { id: "anim_ink_papercut",      category: "anim", thumbnail: "/style-thumbnails/anim_ink_papercut.png" },
  { id: "anim_felt",              category: "anim", thumbnail: "/style-thumbnails/anim_felt.png" },
  { id: "anim_clay",              category: "anim", thumbnail: "/style-thumbnails/anim_clay.png" },
  { id: "anim_jp_horror",         category: "anim", thumbnail: "/style-thumbnails/anim_jp_horror.png" },
  { id: "anim_kr_webtoon",        category: "anim", thumbnail: "/style-thumbnails/anim_kr_webtoon.png" },
  { id: "anim_zzz",               category: "anim", thumbnail: "/style-thumbnails/anim_zzz.png" },
  { id: "anim_ghibli",            category: "anim", thumbnail: "/style-thumbnails/anim_ghibli.png" },
  { id: "anim_demon_slayer",      category: "anim", thumbnail: "/style-thumbnails/anim_demon_slayer.png" },
  { id: "anim_cyberpunk",         category: "anim", thumbnail: "/style-thumbnails/anim_cyberpunk.png" },
  { id: "anim_bloodborne",        category: "anim", thumbnail: "/style-thumbnails/anim_bloodborne.png" },
  { id: "anim_itojunji",          category: "anim", thumbnail: "/style-thumbnails/anim_itojunji.png" },
  { id: "anim_90s_retro",         category: "anim", thumbnail: "/style-thumbnails/anim_90s_retro.png" },
];

export const DEFAULT_TEMPLATE_ID = "live_premium_drama";

export function getTemplatesByCategory(cat: StyleCategory): StyleTemplate[] {
  return STYLE_TEMPLATES.filter((t) => t.category === cat);
}
