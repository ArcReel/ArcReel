/**
 * 演示数据的配图：按名字现算一张 SVG，以 data URI 形式交给现有的 <img>。
 *
 * 引导要展示一个「推进到一半」的项目，每个角色、场景、道具和分镜都得有画面。这些画面
 * 不能是真图 —— 二进制大图入库会随分辨率和数量膨胀仓库，也没法随主题色走。所以这里按
 * 资源名派生色相、按类别画一套几何母题，四类共用同一个取景标记（四角 L 形），让人一眼
 * 看出这些格子里放的是「还没拍的镜头」而不是加载失败。
 *
 * 母题都收在画布中央 60% 的范围内：消费方的画框比例从 1:1 到 9:16 不等，都用
 * object-cover 裁切，只有居中的构图能在各比例下都完整。
 */

import { hashHue } from "@/components/ui/darkroom-tokens";

export type DemoPlaceholderMotif = "character" | "scene" | "prop" | "storyboard";

/** 取景标记：四角 L 形，四类母题共用的签名元素。 */
const FRAMING_BRACKETS = [
  "M26 33 L26 26 L33 26",
  "M67 26 L74 26 L74 33",
  "M74 67 L74 74 L67 74",
  "M33 74 L26 74 L26 67",
]
  .map((d) => `<path d="${d}" fill="none" stroke="var(--pl-line)" stroke-width="1.1"/>`)
  .join("");

/** 各类别的几何母题。坐标系为 100×100，构图收在中央。 */
const MOTIFS: Record<DemoPlaceholderMotif, string> = {
  // 半身像：头 + 肩线
  character:
    '<circle cx="50" cy="40" r="10" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>' +
    '<path d="M32 72 Q50 52 68 72" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>',
  // 外景：地平线 + 远山 + 天体
  scene:
    '<path d="M28 62 H72" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>' +
    '<path d="M32 62 L44 44 L56 62 Z M52 62 L62 48 L72 62 Z" fill="none" stroke="var(--pl-line)" stroke-width="1.4"/>' +
    '<circle cx="64" cy="38" r="5" fill="var(--pl-line)" opacity="0.5"/>',
  // 器物：菱形嵌套
  prop:
    '<path d="M50 30 L70 50 L50 70 L30 50 Z" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>' +
    '<path d="M50 41 L59 50 L50 59 L41 50 Z" fill="var(--pl-line)" opacity="0.35"/>',
  // 镜头：三分线 + 对角构图线 + 视觉焦点
  storyboard:
    '<path d="M43 30 V70 M57 30 V70 M30 43 H70 M30 57 H70" fill="none" stroke="var(--pl-line)" stroke-width="0.8" opacity="0.55"/>' +
    '<path d="M30 70 L70 30" fill="none" stroke="var(--pl-line)" stroke-width="1.2"/>' +
    '<circle cx="57" cy="43" r="4.5" fill="none" stroke="var(--pl-line)" stroke-width="1.6"/>',
};

/**
 * 按 seed 现算一张占位图，返回可直接放进 `<img src>` 的 data URI。
 *
 * @param seed 资源名。同名恒得同图，界面重渲染不会换色。
 * @param motif 类别母题。
 */
export function demoPlaceholder(seed: string, motif: DemoPlaceholderMotif): string {
  const hue = hashHue(seed, 17);
  const hue2 = (hue + 40) % 360;
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="presentation" ' +
    `style="--pl-line:oklch(0.92 0.03 ${hue} / 0.45)">` +
    "<defs>" +
    '<radialGradient id="g" cx="32%" cy="26%" r="92%">' +
    `<stop offset="0%" stop-color="oklch(0.46 0.11 ${hue})"/>` +
    `<stop offset="52%" stop-color="oklch(0.27 0.06 ${hue2})"/>` +
    '<stop offset="100%" stop-color="oklch(0.15 0.02 265)"/>' +
    "</radialGradient>" +
    "</defs>" +
    '<rect width="100" height="100" fill="url(#g)"/>' +
    FRAMING_BRACKETS +
    MOTIFS[motif] +
    "</svg>";
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
