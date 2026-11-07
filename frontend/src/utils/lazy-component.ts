import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * Chunks already causing whole-page refreshes this session, keyed by exports, preventing infinite refreshes when new entrypoints still fail.
 * Per-chunk records: unrelated successes cannot prove failed chunks recovered, never lifting their refresh limits.
 */
const RELOAD_MARK = "arcreel:lazy-chunk-reload";

function reloadedChunks(): Set<string> {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(RELOAD_MARK) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

function markReloaded(name: string, reloaded: boolean) {
  const chunks = reloadedChunks();
  if (reloaded) chunks.add(name);
  else chunks.delete(name);
  if (chunks.size > 0) sessionStorage.setItem(RELOAD_MARK, JSON.stringify([...chunks]));
  else sessionStorage.removeItem(RELOAD_MARK);
}

/** Wait after reload requests before unchanged pages mean blocked refreshes. */
const RELOAD_BLOCKED_AFTER_MS = 3000;

interface LazyNamedOptions {
  /** Whole-page reload implementation, replaceable in tests, default `window.location.reload()`. */
  reload?: () => void;
  /** Wait for page departures before classifying reloads blocked; tests shorten this. */
  reloadBlockedAfterMs?: number;
}

/**
 * Request whole-page reloads and determine whether they occur.
 *
 * Unsaved-change guards preventDefault in `beforeunload`, opening browser confirmations;
 * users choosing Stay block reloads. Register the last `beforeunload` listener immediately before reload:
 * unprevented events mean departure, returning true; prevented/absent events wait briefly,
 * returning false if pages remain. Confirmation blocks main threads, so timers expire only after user answers.
 */
async function reloadAndConfirm(reload: () => void, blockedAfterMs: number): Promise<boolean> {
  let unloading = false;
  const onBeforeUnload = (event: BeforeUnloadEvent) => {
    unloading = !event.defaultPrevented;
  };
  window.addEventListener("beforeunload", onBeforeUnload);
  try {
    reload();
    await new Promise((resolve) => setTimeout(resolve, blockedAfterMs));
    return unloading;
  } finally {
    window.removeEventListener("beforeunload", onBeforeUnload);
  }
}

/**
 * Lazy-load named exports with `<Suspense>`, splitting noninitial pages into separate chunks.
 *
 * Chunk failures commonly follow deployments: old entries reference replaced hash filenames,
 * returning 404. Refresh whole pages once for new entries; same-session repeated failures propagate to
 * React without another reload. Later successful loads clear this chunk's marker, allowing next-deployment refreshes. Reloads blocked by
 * leave confirmations protecting unsaved edits also throw and clear markers.
 *
 * Outer `LazyBoundary` (`components/shared/LazyBoundary`) catches failures, showing errors/
 * Reload. Call sites must wrap boundaries or errors unmount whole application roots.
 *
 * Returned `preload()` fetches chunks before imminent navigation (onboarding crossing to settings),
 * sharing render requests. Prefetch failures stay silent, deferring handling to rendering rules above.
 */
export function lazyNamed<K extends string, P extends object>(
  load: () => Promise<Record<K, ComponentType<P>>>,
  name: K,
  { reload = () => window.location.reload(), reloadBlockedAfterMs = RELOAD_BLOCKED_AFTER_MS }: LazyNamedOptions = {},
): LazyExoticComponent<ComponentType<P>> & { preload: () => void } {
  let pending: Promise<Record<K, ComponentType<P>>> | null = null;
  const loadOnce = () =>
    (pending ??= load().catch((err: unknown) => {
      // Never cache failed requests; later renders/prefetches try again.
      pending = null;
      throw err;
    }));
  const component = lazy(async () => {
    try {
      const mod = await loadOnce();
      markReloaded(name, false);
      return { default: mod[name] };
    } catch (err) {
      if (reloadedChunks().has(name)) throw err;
      markReloaded(name, true);
      if (await reloadAndConfirm(reload, reloadBlockedAfterMs)) {
        // Remain suspended during reloads, retaining Suspense placeholders without error flashes.
        return new Promise<never>(() => {});
      }
      // Blocked refreshes retain old pages; clear markers for later refresh attempts, displaying errors through boundaries.
      markReloaded(name, false);
      throw err;
    }
  });
  return Object.assign(component, {
    preload: () => {
      loadOnce().catch(() => {});
    },
  });
}
