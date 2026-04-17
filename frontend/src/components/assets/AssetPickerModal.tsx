import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Search, X } from "lucide-react";
import { API } from "@/api";
import type { Asset, AssetType } from "@/types/asset";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { AssetThumb } from "./AssetThumb";

interface Props {
  type: AssetType;
  existingNames: Set<string>;
  onClose: () => void;
  onImport: (assetIds: string[]) => void;
}

const PAGE_SIZE = 50;

export function AssetPickerModal({ type, existingNames, onClose, onImport }: Props) {
  const { t } = useTranslation("assets");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [q, setQ] = useState("");
  const debouncedQ = useDebouncedValue(q, 250);
  const [selected, setSelected] = useState<Map<string, Asset>>(new Map());
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef);

  useEscapeClose(onClose);

  useEffect(() => {
    const ctrl = new AbortController();
    void (async () => {
      setLoading(true);
      try {
        const res = await API.listAssets(
          { type, q: debouncedQ || undefined, limit: PAGE_SIZE, offset: 0 },
          { signal: ctrl.signal },
        );
        if (!ctrl.signal.aborted) {
          setAssets(res.items);
          setHasMore(res.items.length === PAGE_SIZE);
          setLoading(false);
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError" && !ctrl.signal.aborted) {
          setLoading(false);
        }
      }
    })();
    return () => ctrl.abort();
  }, [type, debouncedQ]);

  const assetsWithUrl = useMemo(
    () => assets.map((a) => ({ asset: a, url: API.getGlobalAssetUrl(a.image_path, a.updated_at) })),
    [assets],
  );

  const loadMore = async () => {
    setLoading(true);
    const res = await API.listAssets({ type, q: debouncedQ || undefined, limit: PAGE_SIZE, offset: assets.length });
    setAssets((prev) => [...prev, ...res.items]);
    setHasMore(res.items.length === PAGE_SIZE);
    setLoading(false);
  };

  const toggle = (a: Asset, disabled: boolean) => {
    if (disabled) return;
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(a.id)) next.delete(a.id); else next.set(a.id, a);
      return next;
    });
  };

  const titleKey = `picker_title_${type}` as const;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <button
        type="button"
        aria-label={t("close")}
        onClick={onClose}
        className="absolute inset-0 bg-black/70"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t(titleKey)}
        className="relative w-[720px] max-w-[96vw] max-h-[90vh] flex flex-col rounded-lg bg-gray-900 border border-gray-700 shadow-2xl"
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
          <h3 className="text-sm font-semibold text-white flex-1">{t(titleKey)}</h3>
          <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded w-48">
            <Search className="h-3.5 w-3.5 text-gray-500" />
            <input type="text" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder={t("search_placeholder")}
              className="flex-1 bg-transparent text-sm text-gray-200 outline-none" />
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 grid grid-cols-4 gap-2">
          {assetsWithUrl.map(({ asset: a, url }) => {
            const dup = existingNames.has(a.name);
            const sel = selected.has(a.id);
            return (
              <button
                key={a.id}
                type="button"
                disabled={dup}
                aria-pressed={sel}
                onClick={() => toggle(a, dup)}
                className={`relative rounded border p-2 text-left transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none ${
                  dup
                    ? "opacity-40 cursor-not-allowed border-gray-700 bg-gray-800"
                    : sel
                      ? "border-indigo-500 bg-indigo-950"
                      : "border-gray-700 bg-gray-800 hover:border-gray-600"
                }`}
              >
                <AssetThumb imageUrl={url} alt={a.name} fallback="—" variant="picker" />
                <div className="mt-1 text-xs font-semibold text-white truncate">{a.name}</div>
                {a.description && <div className="text-[10px] text-gray-400 truncate">{a.description}</div>}
                {dup && (
                  <span className="absolute top-1 right-1 text-[9px] px-1 py-0.5 bg-amber-900 text-amber-200 rounded">
                    {t("already_in_project")}
                  </span>
                )}
              </button>
            );
          })}
          {hasMore && (
            <div className="col-span-4 flex justify-center py-2">
              <button
                type="button"
                onClick={() => void loadMore()}
                disabled={loading}
                className="px-3 py-1 text-xs rounded bg-gray-800 text-gray-300 border border-gray-700 hover:bg-gray-700 disabled:opacity-50"
              >
                {loading ? t("loading") : t("load_more")}
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-800">
          <span className="text-xs text-gray-400 flex-1">
            {t("import_count", { count: selected.size })}
          </span>
          <button type="button" onClick={onClose} className="px-3 py-1 text-xs rounded bg-gray-800 text-gray-300">
            {t("cancel")}
          </button>
          <button
            type="button"
            disabled={selected.size === 0}
            onClick={() => onImport(Array.from(selected.keys()))}
            className="px-3 py-1 text-xs rounded bg-indigo-600 text-white disabled:opacity-50"
          >
            {selected.size === 0 ? t("confirm_import") : t("import_count", { count: selected.size })}
          </button>
        </div>
      </div>
    </div>
  );
}
