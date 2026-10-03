import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { Asset, AssetListPage, AssetType } from "@/types/asset";

/** 资产库每页条数。 */
export const ASSET_PAGE_SIZE = 60;

interface PagesState {
  /** 这份数据对应的类型与搜索词；与当前查询不一致时说明首页还在加载。 */
  key: string | null;
  items: Asset[];
  total: number;
  counts: AssetListPage["counts"] | null;
  error: string | null;
  loadingMore: boolean;
  moreError: string | null;
}

const INITIAL: PagesState = {
  key: null,
  items: [],
  total: 0,
  counts: null,
  error: null,
  loadingMore: false,
  moreError: null,
};

/** 与后端搜索同口径（名称包含搜索词，ASCII 不区分大小写），用于判断本地新建的条目是否计入当前结果。 */
function matchesQuery(asset: Asset, q: string): boolean {
  return !q || asset.name.toLowerCase().includes(q.toLowerCase());
}

function bump(counts: PagesState["counts"], type: AssetType, delta: number): PagesState["counts"] {
  return counts ? { ...counts, [type]: Math.max(0, counts[type] + delta) } : counts;
}

export interface AssetPages {
  /** 已加载的条目；首页加载中为空。 */
  items: Asset[];
  /** 当前类型与搜索词下的匹配总数。 */
  total: number;
  /** 当前搜索词下各类型的匹配数，换搜索词时保留上一次的数字直到新结果到达。 */
  counts: AssetListPage["counts"] | null;
  loading: boolean;
  /** 首页加载失败的原因。 */
  error: string | null;
  loadingMore: boolean;
  /** 加载下一页失败的原因。 */
  moreError: string | null;
  hasMore: boolean;
  loadMore: () => void;
  /** 重新加载首页（首页失败后）或下一页（翻页失败后）。 */
  retry: () => void;
  /** 本地新建的条目：符合当前类型与搜索词时插到最前，并更新计数。 */
  add: (asset: Asset) => void;
  /** 本地更新的条目：按更新时间排在最前，与后端排序一致。 */
  update: (asset: Asset) => void;
  remove: (asset: Asset) => void;
}

/**
 * 按 offset 分页读取资产库，类型或搜索词变化时从第一页重新加载。
 * 本地增删改同步调整已加载条目与计数，后续页的 offset 与后端保持一致。
 */
export function useAssetPages({ type, q }: { type?: AssetType; q: string }): AssetPages {
  const key = JSON.stringify([type ?? null, q]);
  const [state, setState] = useState<PagesState>(INITIAL);
  const [reloadToken, setReloadToken] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  const current = state.key === key;

  const fetchPage = useCallback(
    (offset: number, signal: AbortSignal) =>
      API.listAssets({ type, q: q || undefined, limit: ASSET_PAGE_SIZE, offset }, { signal }),
    [type, q],
  );

  useEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    fetchPage(0, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        setState({ ...INITIAL, key, items: page.items, total: page.total, counts: page.counts });
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setState((prev) => ({ ...INITIAL, key, counts: prev.counts, error: errMsg(err) }));
      },
    );
    return () => controller.abort();
  }, [fetchPage, key, reloadToken]);

  const hasMore = current && !state.error && state.items.length < state.total;

  const loadMore = useCallback(() => {
    if (!hasMore || state.loadingMore || state.moreError) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState((prev) => ({ ...prev, loadingMore: true, moreError: null }));
    fetchPage(state.items.length, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        setState((prev) => {
          const seen = new Set(prev.items.map((item) => item.id));
          const fresh = page.items.filter((item) => !seen.has(item.id));
          return { ...prev, items: [...prev.items, ...fresh], total: page.total, counts: page.counts, loadingMore: false };
        });
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setState((prev) => ({ ...prev, loadingMore: false, moreError: errMsg(err) }));
      },
    );
  }, [fetchPage, hasMore, state.items.length, state.loadingMore, state.moreError]);

  const retry = useCallback(() => {
    if (state.moreError) {
      setState((prev) => ({ ...prev, moreError: null }));
      return;
    }
    setState((prev) => ({ ...prev, key: null, error: null }));
    setReloadToken((n) => n + 1);
  }, [state.moreError]);

  const add = useCallback(
    (asset: Asset) => {
      if (!matchesQuery(asset, q)) return;
      setState((prev) => {
        const counts = bump(prev.counts, asset.type, 1);
        if (type && asset.type !== type) return { ...prev, counts };
        return { ...prev, counts, items: [asset, ...prev.items], total: prev.total + 1 };
      });
    },
    [q, type],
  );

  const update = useCallback((asset: Asset) => {
    setState((prev) => ({ ...prev, items: [asset, ...prev.items.filter((item) => item.id !== asset.id)] }));
  }, []);

  const remove = useCallback((asset: Asset) => {
    setState((prev) => {
      if (!prev.items.some((item) => item.id === asset.id)) return prev;
      return {
        ...prev,
        items: prev.items.filter((item) => item.id !== asset.id),
        total: Math.max(0, prev.total - 1),
        counts: bump(prev.counts, asset.type, -1),
      };
    });
  }, []);

  return {
    items: current ? state.items : [],
    total: current ? state.total : 0,
    counts: state.counts,
    loading: !current,
    error: current ? state.error : null,
    loadingMore: current && state.loadingMore,
    moreError: current ? state.moreError : null,
    hasMore,
    loadMore,
    retry,
    add,
    update,
    remove,
  };
}
