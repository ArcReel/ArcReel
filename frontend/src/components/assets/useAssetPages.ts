import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { Asset, AssetListPage, AssetType } from "@/types/asset";

/** Asset-library page size. */
export const ASSET_PAGE_SIZE = 60;

interface AssetQuery {
  type?: AssetType;
  q: string;
}

interface PagesState {
  /** Type/search corresponding to these data; disagreement with current queries means first pages are still loading. */
  key: string | null;
  /** Query corresponding to key; local additions/updates use it so late callbacks cannot overwrite new lists using stale closure queries. */
  query: AssetQuery | null;
  items: Asset[];
  total: number;
  /** Consumed server positions; duplicate entries still occupy offsets. */
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

/** Match backend search (same types, names containing search terms, ASCII case-insensitive) to determine whether local additions/updates enter current results. */
function matchesQuery(asset: Asset, { type, q }: AssetQuery): boolean {
  return (!type || asset.type === type) && (!q || asset.name.toLowerCase().includes(q.toLowerCase()));
}

function bump(counts: PagesState["counts"], type: AssetType, delta: number): PagesState["counts"] {
  return counts ? { ...counts, [type]: Math.max(0, counts[type] + delta) } : counts;
}

export interface AssetPages {
  /** Loaded entries; empty while first pages load. */
  items: Asset[];
  /** Total matches for current types/search terms. */
  total: number;
  /** Per-type matches for current searches; retain previous counts across search changes until new results arrive. */
  counts: AssetListPage["counts"] | null;
  loading: boolean;
  /** First-page load failure causes. */
  error: string | null;
  loadingMore: boolean;
  /** Next-page load failure causes. */
  moreError: string | null;
  hasMore: boolean;
  loadMore: () => void;
  /** Reload first pages after initial failures, or next pages after pagination failures. */
  retry: () => void;
  /** Locally created entries: prepend when matching current types/search terms and update counts. */
  add: (asset: Asset) => void;
  /** Locally updated entries: newest-updated first, matching backend order. */
  update: (asset: Asset) => void;
  remove: (asset: Asset) => void;
}

/**
 * Read asset libraries with offset pagination; reload from first pages when types/search terms change.
 * Local additions/deletions/updates adjust loaded entries/counts. Advance positions by raw response lengths; deduplication never rewinds offsets.
 */
export function useAssetPages({ type, q }: { type?: AssetType; q: string }): AssetPages {
  const key = JSON.stringify([type ?? null, q]);
  const [state, setState] = useState<PagesState>(INITIAL);
  const [reloadToken, setReloadToken] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  /** Pending first-page requests; reload after local additions/deletions/updates. */
  const firstRef = useRef<AbortController | null>(null);
  /** Pending next-page requests; invalidate after local additions/deletions. */
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
   * Local additions/deletions/updates and pending requests have unknown ordering: servers may query before or after persisting mutations.
   * Pending first-page responses may omit mutations, and mutations have no lists to merge into, so reload first pages.
   * For pending next pages, advancing offsets under either ordering may skip entries; invalidate requests so sentinels reload at corrected positions.
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

  /** Handle loaded entries only: unloaded entries already count toward total, so insertion/count reduction would misalign counts and lists. */
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
