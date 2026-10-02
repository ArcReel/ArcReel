// PROTOTYPE — 项目大厅与新建项目向导（#2979）。不合并，只在 prototype/2979-lobby 分支上。
// 「大厅三个形态加现状，向导两个形态加现状，同一路由 /app/projects，参数全在地址栏。」
//   ?variant=A|B|C|D      大厅：A 现状；B 海报卡；C 横排紧凑卡；D 海报铺满卡
//   &tag=badge|none        风格标签：海报角标 / 删除
//   &empty=1               强制空状态（不改数据）
//   &wizard=A|B|C          向导：A 现状；B 顶部步骤条；C 左侧步骤栏
//   &wh=fixed|auto         向导高度：固定 / 随内容
//   &open=1                打开向导（刷新后保持打开）

import { useSearch } from "wouter";

export const LOBBY_VARIANTS = {
  A: "现状",
  B: "海报卡",
  C: "横排紧凑卡",
  D: "海报铺满卡",
} as const;

export const WIZARD_VARIANTS = {
  A: "现状",
  B: "顶部步骤条",
  C: "左侧步骤栏 + 摘要",
} as const;

export type LobbyVariant = keyof typeof LOBBY_VARIANTS;
export type WizardVariant = keyof typeof WIZARD_VARIANTS;

export function useProtoParams() {
  const params = new URLSearchParams(useSearch());
  const v = params.get("variant");
  const w = params.get("wizard");
  return {
    variant: (v && v in LOBBY_VARIANTS ? v : "B") as LobbyVariant,
    tag: params.get("tag") === "none" ? ("none" as const) : ("badge" as const),
    empty: params.get("empty") === "1",
    wizard: (w && w in WIZARD_VARIANTS ? w : "B") as WizardVariant,
    wizardHeight: params.get("wh") === "auto" ? ("auto" as const) : ("fixed" as const),
    open: params.get("open") === "1",
  };
}

export function setProtoParam(key: string, value: string | null) {
  const url = new URL(window.location.href);
  if (value === null) url.searchParams.delete(key);
  else url.searchParams.set(key, value);
  // wouter 给 history.replaceState 打了补丁，useSearch 会随之更新
  window.history.replaceState(window.history.state, "", url);
}

export const PROTO_ENABLED = import.meta.env.DEV;
