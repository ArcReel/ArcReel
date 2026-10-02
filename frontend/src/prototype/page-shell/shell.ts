// PROTOTYPE — 页面容器档位与外壳契约（#2969）。不合并，只在 prototype/2969-page-shell 分支上。
// 三个外壳变体由地址栏 ?variant=A|B|C 切换，?guides=1 打开辅助线。

import { createContext, useContext } from "react";
import { useLocation, useSearch } from "wouter";

export type ShellVariant = "A" | "B" | "C";
export const VARIANT_KEYS: ShellVariant[] = ["A", "B", "C"];
export const VARIANT_NAMES: Record<ShellVariant, string> = {
  A: "顶栏贯通 · 左对齐",
  B: "居中组块",
  C: "侧栏头 · 无顶栏",
};

/** 容器档位。阅读与表单限宽并靠左（A、C）或随组块居中（B）；宽档铺满；全出血连内边距也不要。 */
export type Tier = "reading" | "form" | "wide" | "bleed";
export const TIER_NAMES: Record<Tier, string> = {
  reading: "阅读 · 720",
  form: "表单 · 760",
  wide: "宽 · 铺满",
  bleed: "全出血",
};
/** 内容列的最大宽度。wide 与 bleed 不设上限。 */
export const TIER_MAX: Record<Tier, string> = {
  reading: "max-w-[720px]",
  form: "max-w-[760px]",
  wide: "",
  bleed: "",
};

export function useShellParams() {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const raw = params.get("variant");
  const variant: ShellVariant = raw === "B" || raw === "C" ? raw : "A";
  const guides = params.get("guides") === "1";
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(search);
    if (value === null) next.delete(key);
    else next.set(key, value);
    navigate(`${location}?${next.toString()}`, { replace: true });
  };
  return {
    variant,
    guides,
    cycle: (dir: 1 | -1) => {
      const i = VARIANT_KEYS.indexOf(variant);
      set("variant", VARIANT_KEYS[(i + dir + VARIANT_KEYS.length) % VARIANT_KEYS.length]);
    },
    toggleGuides: () => set("guides", guides ? null : "1"),
  };
}

/** 外壳提供的吸底保存栏插槽。区段里的保存栏通过 portal 渲染进来，由外壳决定它横跨多宽、与哪一列对齐。 */
export const FooterSlotContext = createContext<HTMLElement | null>(null);
export const useFooterSlot = () => useContext(FooterSlotContext);
