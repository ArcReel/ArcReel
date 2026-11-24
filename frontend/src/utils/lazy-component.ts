import { lazy, type ComponentType, type LazyExoticComponent } from "react";

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

const RELOAD_BLOCKED_AFTER_MS = 3000;

interface LazyNamedOptions {
  reload?: () => void;
  reloadBlockedAfterMs?: number;
}

/**
 * 发起整页刷新并判断它是否真的会发生。
 *
 * 有未保存修改时离开拦截会在 `beforeunload` 里 preventDefault，浏览器弹出离开确认，
 * 用户可以选择留下，刷新就不会发生。这里在刷新前最后注册一个 `beforeunload` 监听：
 * 事件未被拦下说明页面正在离开，返回 true；被拦下或事件未触发时等待一段时间，
 * 页面仍在则返回 false。确认框会阻塞主线程，计时器在用户作答之后才会到期。
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

export function lazyNamed<K extends string, P extends object>(
  load: () => Promise<Record<K, ComponentType<P>>>,
  name: K,
  { reload = () => window.location.reload(), reloadBlockedAfterMs = RELOAD_BLOCKED_AFTER_MS }: LazyNamedOptions = {},
): LazyExoticComponent<ComponentType<P>> & { preload: () => void } {
  let pending: Promise<Record<K, ComponentType<P>>> | null = null;
  const loadOnce = () =>
    (pending ??= load().catch((err: unknown) => {
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
        return new Promise<never>(() => {});
      }
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
