import { useSyncExternalStore } from "react";

/** 时长读数的刷新周期。秒级精度下更密的 tick 只增加重渲染，不增加信息。 */
const TICK_MS = 1000;

/**
 * 全应用共享的「当前时刻」时钟。
 *
 * 计时器是**进程级单例**而非每个订阅者一个：界面上同时可见几十个运行中任务，逐个起
 * `setInterval` 会让同一秒的重渲染分散在几十个互不对齐的时刻上。走 external store 后
 * 定时器随第一个订阅者启动、最后一个卸载时停止，无需各组件自行清理。
 */
const subscribers = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let now = Date.now();

function subscribe(onStoreChange: () => void): () => void {
  subscribers.add(onStoreChange);
  if (timer === null) {
    // 全部订阅者卸载期间时钟不推进，重新订阅时先补齐，否则首个 tick 到来前
    // 读数停在上次停表的时刻。
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      for (const notify of subscribers) notify();
    }, TICK_MS);
  }
  return () => {
    subscribers.delete(onStoreChange);
    if (subscribers.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  return now;
}

/**
 * 每秒推进一次的当前时刻（毫秒）。只在需要随时间自行走动的读数上订阅——已定格的
 * 数值直接用常量时刻计算，订阅它们只会白白每秒重渲染一次。
 */
export function useNowTick(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
