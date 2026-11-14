import { useSyncExternalStore } from "react";

/**
 * 移动档：视口窄于 768px（Tailwind `md`）时成立。
 *
 * 与 `useCompactTier`（1280）分工：紧凑档只改工作区三栏的排布，移动档进一步把
 * 侧栏改成抽屉、Agent 面板改成全屏层，并放大触控目标。外壳与页面骨架是唯一读取
 * 这个档位的地方，其余组件按容器查询或 `max-md:` 变体响应。
 */
export const MOBILE_TIER_QUERY = "(max-width: 47.999rem)";

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(MOBILE_TIER_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isMobileTier(): boolean {
  // 测试环境没有 matchMedia 时按桌面档处理，避免既有用例被迫感知移动布局。
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(MOBILE_TIER_QUERY).matches
  );
}

export function useMobileTier(): boolean {
  return useSyncExternalStore(subscribe, isMobileTier, () => false);
}
