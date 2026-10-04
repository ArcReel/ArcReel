import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { Asset, AssetListPage, AssetType } from "@/types/asset";

/** 资产库每页条数。 */
export const ASSET_PAGE_SIZE = 60;

interface AssetQuery {
  type?: AssetType;
  q: string;
}

interface PagesState {
  /** 这份数据对应的类型与搜索词；与当前查询不一致时说明首页还在加载。 */
  key: string | null;
  /** 与 key 对应的查询。本地增改按它判断，迟到的回调不会按旧闭包里的查询改写新列表。 */
  query: AssetQuery | null;
  items: Asset[];
  total: number;
  /** 已消费的服务端位置，重复条目仍占一个 offset。 */
  nextOffset: number;
  counts: AssetListPage["counts"] | null;
  error: string | null;
  loadingMore: boolean;
  moreError: string | null;
}

const INITIAL: PagesState = {
  key: null,
  query: null,
  items: [],
  total: 0,
  nextOffset: 0,
  counts: null,
  error: null,
  loadingMore: false,
  moreError: null,
};

/** 与后端搜索同口径（类型一致、名称包含搜索词，ASCII 不区分大小写），用于判断本地新建或更新的条目是否计入当前结果。 */
function matchesQuery(asset: Asset, { type, q }: AssetQuery): boolean {
  return (!type || asset.type === type) && (!q || asset.name.toLowerCase().includes(q.toLowerCase()));
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
 * 本地增删改同步调整已加载条目与计数。翻页位置按响应的原始条数推进，去重不回退 offset。
 */
export function useAssetPages({ type, q }: { type?: AssetType; q: string }): AssetPages {
  const key = JSON.stringify([type ?? null, q]);
  const [state, setState] = useState<PagesState>(INITIAL);
  const [reloadToken, setReloadToken] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  /** 在途的首页请求；本地增删改后重新加载。 */
  const firstRef = useRef<AbortController | null>(null);
  /** 在途的下一页请求；本地增删后作废。 */
  const moreRef = useRef<AbortController | null>(null);
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
    firstRef.current = controller;
    fetchPage(0, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        firstRef.current = null;
        setState({ ...INITIAL, key, query: { type, q }, items: page.items, nextOffset: page.items.length, total: page.total, counts: page.counts });
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        firstRef.current = null;
        setState((prev) => ({ ...INITIAL, key, counts: prev.counts, error: errMsg(err) }));
      },
    );
    return () => controller.abort();
  }, [fetchPage, key, type, q, reloadToken]);

  const hasMore = current && !state.error && state.nextOffset < state.total;

  const loadMore = useCallback(() => {
    if (!hasMore || state.loadingMore || state.moreError) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    moreRef.current = controller;
    setState((prev) => ({ ...prev, loadingMore: true, moreError: null }));
    fetchPage(state.nextOffset, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        moreRef.current = null;
        setState((prev) => {
          const seen = new Set(prev.items.map((item) => item.id));
          const fresh = page.items.filter((item) => !seen.has(item.id));
          return {
            ...prev,
            items: [...prev.items, ...fresh],
            nextOffset: page.items.length ? prev.nextOffset + page.items.length : page.total,
            total: page.total,
            counts: page.counts,
            loadingMore: false,
          };
        });
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        moreRef.current = null;
        setState((prev) => ({ ...prev, loadingMore: false, moreError: errMsg(err) }));
      },
    );
  }, [fetchPage, hasMore, state.nextOffset, state.loadingMore, state.moreError]);

  /**
   * 本地增删改与在途请求的先后无从得知：服务端可能先执行查询，也可能先落盘修改。
   * 首页在途时，响应可能缺这次修改，而修改又没有可以合并的列表，所以重新加载首页。
   * 下一页在途时，按任一种顺序推进 offset 都可能跳过一条，所以作废这次请求，哨兵随后按校正后的位置重新加载。
   */
  const settleLocalChange = useCallback((next: (prev: PagesState) => PagesState) => {
    if (firstRef.current) {
      setReloadToken((n) => n + 1);
      return;
    }
    const pending = moreRef.current;
    moreRef.current = null;
    pending?.abort();
    setState((prev) => {
      const changed = next(prev);
      return pending ? { ...changed, loadingMore: false } : changed;
    });
  }, []);

  const retry = useCallback(() => {
    if (state.moreError) {
      setState((prev) => ({ ...prev, moreError: null }));
      return;
    }
    setState((prev) => ({ ...prev, key: null, error: null }));
    setReloadToken((n) => n + 1);
  }, [state.moreError]);

  const add = useCallback(
    (asset: Asset) =>
      settleLocalChange((prev) => {
        if (!prev.query || !matchesQuery(asset, { q: prev.query.q })) return prev;
        const counts = bump(prev.counts, asset.type, 1);
        if (!matchesQuery(asset, prev.query)) return { ...prev, counts };
        return { ...prev, counts, items: [asset, ...prev.items], nextOffset: prev.nextOffset + 1, total: prev.total + 1 };
      }),
    [settleLocalChange],
  );

  /** 只处理已加载的条目：未加载的条目已计入 total，插入或减计数都会让计数与列表错位。 */
  const update = useCallback(
    (asset: Asset) =>
      settleLocalChange((prev) => {
        if (!prev.query || !prev.items.some((item) => item.id === asset.id)) return prev;
        const items = prev.items.filter((item) => item.id !== asset.id);
        if (matchesQuery(asset, prev.query)) return { ...prev, items: [asset, ...items] };
        return {
          ...prev,
          items,
          nextOffset: Math.max(0, prev.nextOffset - 1),
          total: Math.max(0, prev.total - 1),
          counts: bump(prev.counts, asset.type, -1),
        };
      }),
    [settleLocalChange],
  );

  const remove = useCallback(
    (asset: Asset) =>
      settleLocalChange((prev) => {
        if (!prev.items.some((item) => item.id === asset.id)) return prev;
        return {
          ...prev,
          items: prev.items.filter((item) => item.id !== asset.id),
          nextOffset: Math.max(0, prev.nextOffset - 1),
          total: Math.max(0, prev.total - 1),
          counts: bump(prev.counts, asset.type, -1),
        };
      }),
    [settleLocalChange],
  );

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
