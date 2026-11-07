import { useSyncExternalStore } from "react";

/** Duration-readout refresh interval. Denser ticks add rerenders without more information at second-level precision. */
const TICK_MS = 1000;

/**
 * Application-wide shared current-time clock.
 *
 * Timer is a **process singleton**, never one per subscriber: dozens of running tasks may be visible, and individual
 * `setInterval` timers would scatter same-second rerenders across dozens of unaligned instants. An external store starts
 * timers with the first subscriber and stops after the last unmounts, without component-specific cleanup.
 */
const subscribers = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let now = Date.now();

function subscribe(onStoreChange: () => void): () => void {
  subscribers.add(onStoreChange);
  if (timer === null) {
    // Clocks stop while every subscriber is unmounted. Catch up immediately on resubscription, or readings remain at
    // the last stopped instant until the first tick.
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
 * Current time in milliseconds, advanced once per second. Subscribe only for readouts requiring autonomous time updates; frozen
 * values use constant instants directly, otherwise subscriptions merely waste one rerender per second.
 */
export function useNowTick(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
