import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * 本会话已因加载失败整页刷新过的 chunk（按导出名记录），防止新入口仍拉不到时无限刷新。
 * 按 chunk 记录：别的 chunk 加载成功不代表失败的那个已经恢复，不能借此解除它的刷新限制。
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

/** 发起刷新后页面仍未离开、判定刷新被拦下的等待时长。 */
const RELOAD_BLOCKED_AFTER_MS = 3000;

interface LazyNamedOptions {
  /** 整页刷新的实现，测试替换；默认 `window.location.reload()`。 */
  reload?: () => void;
  /** 发起刷新后等待多久仍未离开页面，即视为刷新被拦下；测试缩短。 */
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

/**
 * 按命名导出懒加载组件，配合 `<Suspense>` 把非首屏页面拆进独立 chunk。
 *
 * chunk 拉取失败多发生在新版本部署之后：页面仍是旧入口，引用的带 hash 文件名已被替换，
 * 请求返回 404。此时整页刷新一次换上新入口；同一会话内该 chunk 刷新后仍失败，错误照常抛给
 * React，不再刷新。该 chunk 之后加载成功即清除它的标记，下次部署后的失败仍可刷新一次。刷新被
 * 离开确认拦下（用户为保护未保存修改选择留下）时同样抛出错误并清除标记。
 *
 * 抛出的错误由外层 `LazyBoundary`（`components/shared/LazyBoundary`）接住，显示加载失败与
 * 「重新加载」；使用处必须包在它里面，否则错误会卸载整个应用根节点。
 *
 * 返回值额外带 `preload()`：已知马上要进入该页面时（如引导即将跨到设置页）提前拉取 chunk，
 * 与渲染时的加载共用同一个请求；预取失败不报错，留给渲染时按上述规则处理。
 */
export function lazyNamed<K extends string, P extends object>(
  load: () => Promise<Record<K, ComponentType<P>>>,
  name: K,
  { reload = () => window.location.reload(), reloadBlockedAfterMs = RELOAD_BLOCKED_AFTER_MS }: LazyNamedOptions = {},
): LazyExoticComponent<ComponentType<P>> & { preload: () => void } {
  let pending: Promise<Record<K, ComponentType<P>>> | null = null;
  const loadOnce = () =>
    (pending ??= load().catch((err: unknown) => {
      // 失败的请求不缓存，下次渲染或预取重新发起
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
        // 刷新期间保持挂起，界面停在 Suspense 的占位上，不闪错误
        return new Promise<never>(() => {});
      }
      // 刷新被拦下，本页不会换新：清除标记，让之后的失败仍能刷新，错误交给边界显示
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
