// PROTOTYPE — 广告项目视频页的「故事设定」tab 状态（#2981），不合并。
// 维护者决定：广告项目的概览不显示故事设定，改由视频页 tab 承载。原型用模块级状态，正式实现并入集页的 ?view=。

import { useSyncExternalStore } from "react";

let active = false;
const listeners = new Set<() => void>();

export function setAdSettingTab(next: boolean) {
  if (active === next) return;
  active = next;
  for (const l of listeners) l();
}

export function useAdSettingTab() {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => active,
  );
}
