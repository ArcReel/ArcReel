import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Library, Search, Check } from "lucide-react";
import { API } from "@/api";
import type { Asset, AssetType } from "@/types/asset";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { AssetThumb } from "./AssetThumb";

interface Props {
  type: AssetType;
  existingNames: Set<string>;
  onClose: () => void;
  onImport: (assetIds: string[]) => void;
}

const PAGE_SIZE = 50;

export function AssetPickerModal({ type, existingNames, onClose, onImport }: Props) {
  const { t } = useTranslation(["assets", "dashboard"]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [q, setQ] = useState("");
  const debouncedQ = useDebouncedValue(q, 250);
  const [selected, setSelected] = useState<Map<string, Asset>>(new Map());
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const titleId = useId();
  const loadMoreCtrlRef = useRef<AbortController | null>(null);

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
    return () => {
      ctrl.abort();
      loadMoreCtrlRef.current?.abort();
    };
  }, [type, debouncedQ]);

  const assetsWithUrl = useMemo(
    () => assets.map((a) => ({ asset: a, url: API.getGlobalAssetUrl(a.image_path, a.updated_at) })),
    [assets],
  );

  const loadMore = async () => {
    loadMoreCtrlRef.current?.abort();
    const ctrl = new AbortController();
    loadMoreCtrlRef.current = ctrl;
    setLoading(true);
    try {
      const res = await API.listAssets(
        { type, q: debouncedQ || undefined, limit: PAGE_SIZE, offset: assets.length },
        { signal: ctrl.signal },
      );
      if (!ctrl.signal.aborted) {
        setAssets((prev) => [...prev, ...res.items]);
        setHasMore(res.items.length === PAGE_SIZE);
        setLoading(false);
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError" && !ctrl.signal.aborted) {
        setLoading(false);
      }
    }
  };

  const toggle = (a: Asset, disabled: boolean) => {
    if (disabled) return;
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(a.id)) next.delete(a.id);
      else next.set(a.id, a);
      return next;
    });
  };

  const titleKey = `picker_title_${type}` as const;

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-[760px] max-w-[96vw]"
      panelClassName="flex max-h-[90vh] flex-col"
    >
      {/* Header */}
        <div
          className="flex items-center gap-3 px-5 py-4"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
              boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
            }}
          >
            <Library className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3
              id={titleId}
              className="display-serif truncate text-[15px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t(titleKey)}
            </h3>
            <div
              className="num text-[10px] uppercase"
              style={{
                color: "var(--color-text-4)",
                letterSpacing: "1.0px",
              }}
            >
              {t("dashboard:eyebrow_library", { type: t(`type.${type}`) })}
            </div>
          </div>

          <div
            className="flex w-52 items-center gap-2 rounded-md px-2.5 py-1.5"
            style={{
              background: "oklch(0.16 0.010 265 / 0.6)",
              border: "1px solid var(--color-hairline)",
            }}
          >
            <Search
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "var(--color-text-4)" }}
            />
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("search_placeholder")}
              aria-label={t("search_placeholder")}
              className="focus-ring min-w-0 flex-1 bg-transparent text-[13px] outline-none"
              style={{ color: "var(--color-text)" }}
            />
          </div>

          <ModalCloseButton onClick={onClose} />
        </div>

        {/* Grid */}
        <div className="grid flex-1 grid-cols-4 gap-2 overflow-y-auto p-3">
          {assetsWithUrl.length === 0 && !loading && (
            <div
              className="col-span-4 px-4 py-12 text-center text-[12px]"
              style={{ color: "var(--color-text-4)" }}
            >
              {debouncedQ ? t("no_results") : t("search_hint")}
            </div>
          )}
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
                className="focus-ring relative rounded-lg p-2 text-left transition-colors disabled:cursor-not-allowed"
                style={{
                  border: dup
                    ? "1px solid var(--color-hairline-soft)"
                    : sel
                      ? "1px solid var(--color-accent-soft)"
                      : "1px solid var(--color-hairline)",
                  background: dup
                    ? "oklch(0.20 0.011 265 / 0.3)"
                    : sel
                      ? "linear-gradient(135deg, var(--color-accent-dim) 0%, oklch(0.20 0.011 265 / 0.5) 60%)"
                      : "oklch(0.20 0.011 265 / 0.5)",
                  opacity: dup ? 0.4 : 1,
                  boxShadow: sel
                    ? "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 6px 18px -6px var(--color-accent-glow)"
                    : "inset 0 1px 0 oklch(1 0 0 / 0.03)",
                }}
                onMouseEnter={(e) => {
                  if (!dup && !sel) {
                    e.currentTarget.style.borderColor = "var(--color-hairline-strong)";
                    e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.7)";
                  }
                }}
                onMouseLeave={(e) => {
                  if (!dup && !sel) {
                    e.currentTarget.style.borderColor = "var(--color-hairline)";
                    e.currentTarget.style.background = "oklch(0.20 0.011 265 / 0.5)";
                  }
                }}
              >
                <AssetThumb imageUrl={url} alt={a.name} fallback="—" variant="picker" />
                <div
                  className="mt-1.5 truncate text-[12px] font-semibold"
                  style={{ color: "var(--color-text)" }}
                >
                  {a.name}
                </div>
                {a.description && (
                  <div
                    className="truncate text-[10px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    {a.description}
                  </div>
                )}
                {sel && (
                  <span
                    aria-hidden
                    className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full"
                    style={{
                      color: "oklch(0.14 0 0)",
                      background:
                        "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                      boxShadow:
                        "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 0 0 1px var(--color-accent-soft)",
                    }}
                  >
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </span>
                )}
                {dup && (
                  <span
                    className="num absolute left-1.5 top-1.5 rounded px-1.5 py-0.5 text-[9.5px]"
                    style={{
                      letterSpacing: "0.4px",
                      color: "oklch(0.85 0.13 75)",
                      background: "oklch(0.30 0.10 75 / 0.30)",
                      border: "1px solid oklch(0.45 0.13 75 / 0.40)",
                    }}
                  >
                    {t("already_in_project")}
                  </span>
                )}
              </button>
            );
          })}
          {hasMore && (
            <div className="col-span-4 flex justify-center py-2">
              <SecondaryButton
                size="sm"
                onClick={() => void loadMore()}
                disabled={loading}
              >
                {loading ? t("loading") : t("load_more")}
              </SecondaryButton>
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="flex items-center gap-2 px-5 py-3"
          style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            className="num flex-1 text-[11px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("import_count", { count: selected.size })}
          </span>
          <SecondaryButton size="sm" onClick={onClose}>
            {t("cancel")}
          </SecondaryButton>
          <PrimaryButton
            size="sm"
            disabled={selected.size === 0}
            onClick={() => onImport(Array.from(selected.keys()))}
          >
            <span>{t("confirm_import")}</span>
            {selected.size > 0 && (
              <span
                className="num ml-1.5 rounded px-1.5 py-px text-[10.5px]"
                style={{
                  background: "oklch(0 0 0 / 0.18)",
                  color: "oklch(0.14 0 0)",
                }}
              >
                {selected.size}
              </span>
            )}
          </PrimaryButton>
        </div>
    </GlassModal>
  );
}
