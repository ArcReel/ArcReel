// PROTOTYPE — 资产画廊卡片与编辑方式（#2973）。不合并。
// 原型参数全部放在地址栏，刷新与分享都能复现：
//   ?variant=A|B|C|D   画廊页的编辑方式；资产库页只用 A|B|C
//   &actions=more|pinned  卡片次要操作：全部收进「更多」/ 常驻 2 个图标

import { useSearch } from "wouter";

export const GALLERY_VARIANTS = {
  A: "现状：卡内表单",
  B: "浏览卡 + 右侧 Sheet",
  C: "主从分栏",
  D: "网格内展开",
} as const;

export const LIBRARY_VARIANTS = {
  A: "现状：悬停出编辑 / 删除",
  B: "点击看详情（详情内再编辑）",
  C: "点击直接编辑",
} as const;

export type GalleryVariant = keyof typeof GALLERY_VARIANTS;
export type LibraryVariant = keyof typeof LIBRARY_VARIANTS;
export type ActionsMode = "more" | "pinned";

export function useProtoParams() {
  const search = useSearch();
  const params = new URLSearchParams(search);
  const variant = params.get("variant") ?? "A";
  const actions: ActionsMode = params.get("actions") === "pinned" ? "pinned" : "more";
  return { variant, actions };
}

export function setProtoParam(key: string, value: string) {
  const url = new URL(window.location.href);
  url.searchParams.set(key, value);
  // wouter 给 history.replaceState 打了补丁，useSearch 会随之更新
  window.history.replaceState(window.history.state, "", url);
}

export const PROTO_ENABLED = import.meta.env.DEV;
