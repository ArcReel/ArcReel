// PROTOTYPE — 项目设置页布局（#2971）。不合并，只在 prototype/2971-project-settings 分支上。
// 变体由地址栏 ?variant=A|B|C|D 切换（D 是评审后的推荐组合），A、B 用的分页 ?tab= 也记在地址栏。

import { useLocation, useSearch } from "wouter";

export type PsVariant = "A" | "B" | "C" | "D";
export const PS_VARIANTS: PsVariant[] = ["A", "B", "C", "D"];
export const PS_VARIANT_NAMES: Record<PsVariant, string> = {
  A: "独立外壳 · 侧栏分页",
  B: "独立外壳 · 锚点长页 + 生效摘要",
  C: "嵌进工作区 · 画布内分页",
  D: "推荐：A + 通道来源徽章",
};

export function readPsVariant(search: string): PsVariant {
  const raw = new URLSearchParams(search).get("variant");
  return raw === "B" || raw === "C" || raw === "D" ? raw : "A";
}

export function usePsParams() {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const variant = readPsVariant(search);
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(search);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    navigate(`${location}?${next.toString()}`, { replace: true });
  };
  return {
    variant,
    tab: params.get("tab"),
    guides: params.get("guides") === "1",
    setTab: (tab: string) => set({ tab }),
    cycle: (dir: 1 | -1) => {
      const i = PS_VARIANTS.indexOf(variant);
      set({ variant: PS_VARIANTS[(i + dir + PS_VARIANTS.length) % PS_VARIANTS.length], tab: null });
    },
    toggleGuides: () => set({ guides: params.get("guides") === "1" ? null : "1" }),
  };
}
