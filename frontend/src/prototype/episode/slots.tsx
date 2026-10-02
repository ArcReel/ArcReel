// PROTOTYPE — 剧集页原型（#2974）：页头插槽与画布 tab 的桥接。
// 合并页头时，各画布把自己的集头与批量动作 portal 到路由层页头的插槽里；
// 「脚本规划 / 分镜 / 剪辑」三个 tab 由页头统一渲染，画布内的 tab 状态经这里双向同步。

import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";

type SlotName = "head" | "actions" | "progress";

const slots: Record<SlotName, HTMLElement | null> = { head: null, actions: null, progress: null };
const slotListeners = new Set<() => void>();

export function setSlot(name: SlotName, el: HTMLElement | null) {
  if (slots[name] === el) return;
  slots[name] = el;
  for (const l of slotListeners) l();
}

function useSlot(name: SlotName): HTMLElement | null {
  return useSyncExternalStore(
    (l) => {
      slotListeners.add(l);
      return () => slotListeners.delete(l);
    },
    () => slots[name],
  );
}

/** 插槽存在时 portal 过去；不存在时什么也不渲染。 */
export function SlotPortal({ name, children }: { name: SlotName; children: ReactNode }) {
  const el = useSlot(name);
  return el ? createPortal(children, el) : null;
}

// ---- 画布 tab 桥接 ----

export type CanvasTab = "plan" | "board";

interface TabState {
  current: CanvasTab | null;
  /** 画布有没有「脚本规划」tab（广告类型没有） */
  hasPlan: boolean;
  /** 「分镜」tab 是否可用（没有正式脚本时不可用） */
  boardEnabled: boolean;
  boardLabel: string;
  request: { tab: CanvasTab; id: number } | null;
}

let tabState: TabState = { current: null, hasPlan: false, boardEnabled: false, boardLabel: "分镜", request: null };
const tabListeners = new Set<() => void>();

function setTabState(next: Partial<TabState>) {
  tabState = { ...tabState, ...next };
  for (const l of tabListeners) l();
}

export function useCanvasTabState(): TabState {
  return useSyncExternalStore(
    (l) => {
      tabListeners.add(l);
      return () => tabListeners.delete(l);
    },
    () => tabState,
  );
}

export function requestCanvasTab(tab: CanvasTab) {
  setTabState({ request: { tab, id: (tabState.request?.id ?? 0) + 1 } });
}

/** 画布调用：发布自己的 tab 状态，并响应页头的切换请求。 */
export function useCanvasTabBridge(
  current: CanvasTab,
  info: { hasPlan: boolean; boardEnabled: boolean; boardLabel: string },
  onRequest: (tab: CanvasTab) => void,
) {
  const { hasPlan, boardEnabled, boardLabel } = info;
  useEffect(() => {
    setTabState({ current, hasPlan, boardEnabled, boardLabel });
  }, [current, hasPlan, boardEnabled, boardLabel]);
  useEffect(() => () => setTabState({ current: null }), []);
  const request = useCanvasTabState().request;
  useEffect(() => {
    if (request) onRequest(request.tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应新的请求
  }, [request?.id]);
}
