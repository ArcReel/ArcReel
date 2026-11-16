import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * 按命名导出懒加载组件，配合 `<Suspense>` 把非首屏页面拆进独立 chunk。
 *
 * chunk 拉取失败多发生在新版本部署之后：页面仍是旧入口，引用的带 hash 文件名已被替换，
 * 请求返回 404。这里**不自动整页刷新**——用户可能正看着内容，突然被刷新会丢掉滚动位置、
 * 展开的面板与输入到一半的草稿。失败照常抛给 React，由外层 `LazyBoundary` 就地显示
 * 「页面加载失败」与重新加载按钮，刷新时机交给用户。
 *
 * 抛出的错误由外层 `LazyBoundary`（`components/shared/LazyBoundary`）接住；使用处必须包在
 * 它里面，否则错误会卸载整个应用根节点。
 *
 * 返回值额外带 `preload()`：已知马上要进入该页面时（如引导即将跨到设置页）提前拉取 chunk，
 * 与渲染时的加载共用同一个请求；预取失败不报错，留给渲染时按上述规则处理。
 */
export function lazyNamed<K extends string, P extends object>(
  load: () => Promise<Record<K, ComponentType<P>>>,
  name: K,
): LazyExoticComponent<ComponentType<P>> & { preload: () => void } {
  let pending: Promise<Record<K, ComponentType<P>>> | null = null;
  const loadOnce = () =>
    (pending ??= load().catch((err: unknown) => {
      // 失败的请求不缓存，下次渲染或预取重新发起
      pending = null;
      throw err;
    }));
  const component = lazy(async () => {
    const mod = await loadOnce();
    return { default: mod[name] };
  });
  return Object.assign(component, {
    preload: () => {
      loadOnce().catch(() => {});
    },
  });
}
