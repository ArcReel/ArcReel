import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { API } from "@/api";
import type { Asset, AssetType } from "@/types/asset";

interface Props {
  type: AssetType;
  existingNames: Set<string>;
  onClose: () => void;
  onImport: (assetIds: string[]) => void;
}

export function AssetPickerModal({ type, existingNames, onClose, onImport }: Props) {
  const { t } = useTranslation("assets");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    let disposed = false;
    void (async () => {
      const res = await API.listAssets({ type, q: q || undefined });
      if (!disposed) setAssets(res.items);
    })();
    return () => { disposed = true; };
  }, [type, q]);

  const toggle = (id: string, disabled: boolean) => {
    if (disabled) return;
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const titleKey = `picker_title_${type}` as const;

  return (
    <div role="dialog" className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-[720px] max-w-[96vw] max-h-[90vh] flex flex-col rounded-lg bg-gray-900 border border-gray-700 shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
          <h3 className="text-sm font-semibold text-white flex-1">{t(titleKey)}</h3>
          <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded w-48">
            <Search className="h-3.5 w-3.5 text-gray-500" />
            <input type="text" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder={t("search_placeholder")}
              className="flex-1 bg-transparent text-sm text-gray-200 outline-none" />
          </div>
          <button onClick={onClose} aria-label={t("close")} className="text-gray-500 hover:text-gray-300">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 grid grid-cols-4 gap-2">
          {assets.map((a) => {
            const dup = existingNames.has(a.name);
            const sel = selected.has(a.id);
            const url = API.getGlobalAssetUrl(a.id, a.image_path, a.updated_at);
            return (
              <div key={a.id} role="button" aria-disabled={dup} tabIndex={dup ? -1 : 0}
                onClick={() => toggle(a.id, dup)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle(a.id, dup);
                  }
                }}
                className={`relative rounded border p-2 cursor-pointer transition-colors ${
                  dup ? "opacity-40 cursor-not-allowed" :
                  sel ? "border-indigo-500 bg-indigo-950" : "border-gray-700 bg-gray-800 hover:border-gray-600"
                }`}>
                <div className="aspect-[3/4] bg-gray-700 rounded flex items-center justify-center text-gray-500 text-xs">
                  {url ? <img src={url} alt={a.name} className="h-full w-full object-cover rounded" /> : "—"}
                </div>
                <div className="mt-1 text-xs font-semibold text-white truncate">{a.name}</div>
                {a.description && <div className="text-[10px] text-gray-400 truncate">{a.description}</div>}
                {dup && (
                  <span className="absolute top-1 right-1 text-[9px] px-1 py-0.5 bg-amber-900 text-amber-200 rounded">
                    {t("already_in_project")}
                  </span>
                )}
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-800">
          <span className="text-xs text-gray-400 flex-1">
            {t("import_count", { count: selected.size })}
          </span>
          <button onClick={onClose} className="px-3 py-1 text-xs rounded bg-gray-800 text-gray-300">
            {t("cancel")}
          </button>
          <button disabled={selected.size === 0}
            onClick={() => onImport(Array.from(selected))}
            className="px-3 py-1 text-xs rounded bg-indigo-600 text-white disabled:opacity-50">
            {t("import_count", { count: selected.size })}
          </button>
        </div>
      </div>
    </div>
  );
}
