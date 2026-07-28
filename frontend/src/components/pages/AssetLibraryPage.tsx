import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { ChevronLeft, Landmark, Package as PackageIcon, Plus, Search, User } from "lucide-react";
import { AssetGrid } from "@/components/assets/AssetGrid";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAssetsStore } from "@/stores/assets-store";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { errMsg } from "@/utils/async";
import {
  ACCENT_BTN_CLS,
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  ICON_BTN_FILLED_CLS,
  INPUT_CLS,
  ambientGlowStyle,
} from "@/components/ui/darkroom-tokens";
import type { Asset, AssetType } from "@/types/asset";

const ASSET_LIBRARY_RETURN_TO_KEY = "assetLibrary:returnTo";

/** 入口按钮点击前调用，记录返回目标。只接受应用内部路径，避免 open redirect 风险。 */
export function rememberAssetLibraryReturnTo(pathname: string) {
  if (pathname.startsWith("/app/")) {
    sessionStorage.setItem(ASSET_LIBRARY_RETURN_TO_KEY, pathname);
  }
}

interface TabDef {
  type: AssetType;
  icon: React.ComponentType<{ className?: string }>;
}

const TABS: TabDef[] = [
  { type: "character", icon: User },
  { type: "scene", icon: Landmark },
  { type: "prop", icon: PackageIcon },
];

const EMPTY_KEY: Record<AssetType, string> = {
  character: "library_empty_character",
  scene: "library_empty_scene",
  prop: "library_empty_prop",
};

const HEADER_GLOW_STYLE = ambientGlowStyle({ at: "30% 0%", intensity: 0.08 });

export function AssetLibraryPage() {
  const { t } = useTranslation("assets");
  const [, navigate] = useLocation();
  const search = useSearch();

  const activeTab = useMemo((): AssetType => {
    const tab = new URLSearchParams(search).get("tab");
    return tab === "scene" || tab === "prop" ? tab : "character";
  }, [search]);

  const writeQuery = useCallback((patch: { tab?: AssetType; q?: string }) => {
    const params = new URLSearchParams(window.location.search);
    if (patch.tab !== undefined) {
      if (patch.tab === "character") params.delete("tab");
      else params.set("tab", patch.tab);
    }
    if (patch.q !== undefined) {
      if (patch.q) params.set("q", patch.q);
      else params.delete("q");
    }
    const qs = params.toString();
    navigate(qs ? `${window.location.pathname}?${qs}` : window.location.pathname, { replace: true });
  }, [navigate]);

  const setActiveTab = useCallback((next: AssetType) => writeQuery({ tab: next }), [writeQuery]);

  // WAI-ARIA tablist 键盘导航（issue #488）：roving tabindex + 方向键 + Home/End。
  // 切换后用 requestAnimationFrame 把焦点搬到新激活 tab，避免与 React commit 抢时序。
  const tabRefs = useRef<Map<AssetType, HTMLButtonElement>>(new Map());
  const moveTabFocus = useCallback(
    (next: AssetType) => {
      setActiveTab(next);
      requestAnimationFrame(() => {
        tabRefs.current.get(next)?.focus();
      });
    },
    [setActiveTab],
  );
  const handleTabKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, current: AssetType) => {
      const idx = TABS.findIndex((tab) => tab.type === current);
      if (idx < 0) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        moveTabFocus(TABS[(idx + 1) % TABS.length].type);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        moveTabFocus(TABS[(idx - 1 + TABS.length) % TABS.length].type);
      } else if (event.key === "Home") {
        event.preventDefault();
        moveTabFocus(TABS[0].type);
      } else if (event.key === "End") {
        event.preventDefault();
        moveTabFocus(TABS[TABS.length - 1].type);
      }
    },
    [moveTabFocus],
  );

  const urlQ = useMemo(() => new URLSearchParams(search).get("q") ?? "", [search]);
  const [q, setQ] = useState(urlQ);
  const debouncedQ = useDebouncedValue(q, 250);

  // 浏览器前进/后退或外部地址栏变化导致 urlQ 改变时，把 URL 当前值同步到本地 q。
  useEffect(() => {
    // 外部 URL 变化驱动本地 q 同步，functional setState 已做幂等保护
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQ((prev) => (prev === urlQ ? prev : urlQ));
  }, [urlQ]);

  // debouncedQ 与 URL 不一致时回写，使用 urlQ（已订阅 search）做对比，避免覆盖外部变更。
  useEffect(() => {
    if (urlQ === debouncedQ) return;
    writeQuery({ q: debouncedQ });
  }, [debouncedQ, urlQ, writeQuery]);
  const [formModal, setFormModal] = useState<{ mode: "create" | "edit"; asset?: Asset } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Asset | null>(null);
  const [deleting, setDeleting] = useState(false);

  const byType = useAssetsStore((s) => s.byType);
  const loadList = useAssetsStore((s) => s.loadList);
  const addAsset = useAssetsStore((s) => s.addAsset);
  const updateAssetLocal = useAssetsStore((s) => s.updateAsset);
  const deleteAssetLocal = useAssetsStore((s) => s.deleteAsset);

  useEffect(() => {
    void loadList(activeTab, debouncedQ || undefined);
  }, [activeTab, debouncedQ, loadList]);

  const assets = byType[activeTab];
  const ActiveIcon = TABS.find((tab) => tab.type === activeTab)!.icon;

  const handleSubmit = async (payload: {
    name: string; description: string; voice_style: string; image?: File | null;
  }) => {
    try {
      if (formModal?.mode === "edit" && formModal.asset) {
        const { asset } = await API.updateAsset(formModal.asset.id, {
          name: payload.name, description: payload.description, voice_style: payload.voice_style,
        });
        if (payload.image) {
          const { asset: after } = await API.replaceAssetImage(asset.id, payload.image);
          updateAssetLocal(after);
        } else {
          updateAssetLocal(asset);
        }
      } else {
        const { asset } = await API.createAsset({
          type: activeTab, name: payload.name, description: payload.description,
          voice_style: payload.voice_style, image: payload.image ?? undefined,
        });
        addAsset(asset);
      }
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      throw err; // 让 modal 的 submit 感知失败并保留对话框，用户可修正后重试
    }
  };

  const handleEditAsset = useCallback((a: Asset) => setFormModal({ mode: "edit", asset: a }), []);
  const handleDeleteAsset = useCallback((a: Asset) => setDeleteTarget(a), []);

  const confirmDelete = async () => {
    if (!deleteTarget || deleting) return;
    const asset = deleteTarget;
    setDeleting(true);
    try {
      await deleteAssetLocal(asset.id, asset.type);
      setDeleteTarget(null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="relative flex min-h-screen flex-col bg-bg text-text">
      {/* Decorative ambient glow */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-72" style={HEADER_GLOW_STYLE} />

      <header className="sticky top-0 z-30 border-b border-hairline bg-bg/85 backdrop-blur-[28px]">
        <div className="mx-auto flex max-w-6xl items-start justify-between gap-6 px-6 py-6">
          <div className="flex items-start gap-4">
            <button
              type="button"
              onClick={() => {
                const returnTo = sessionStorage.getItem(ASSET_LIBRARY_RETURN_TO_KEY);
                sessionStorage.removeItem(ASSET_LIBRARY_RETURN_TO_KEY);
                navigate(returnTo && returnTo.startsWith("/app/") ? returnTo : "/app/projects");
              }}
              aria-label={t("back_to_projects")}
              title={t("back_to_projects")}
              className={`mt-1 ${ICON_BTN_FILLED_CLS}`}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <div>
              <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-text-4">
                library · assets
              </div>
              <h1 className="font-editorial mt-0.5 text-[34px] leading-[1.05] tracking-tight text-text">
                {t("library_title")}
              </h1>
              <p className="mt-1.5 text-[13px] text-text-3">{t("library_subtitle")}</p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2 pt-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-4" />
              <input
                type="search"
                aria-label={t("search_placeholder")}
                placeholder={t("search_placeholder")}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                className={`${INPUT_CLS} w-[240px] pl-8`}
              />
            </div>
            <button
              type="button"
              onClick={() => setFormModal({ mode: "create" })}
              className={ACCENT_BTN_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              <Plus className="h-4 w-4" />
              {t("add_asset")}
            </button>
          </div>
        </div>

        <div
          role="tablist"
          aria-orientation="horizontal"
          aria-label={t("library_tabs_label")}
          className="mx-auto flex max-w-6xl items-center gap-2 px-6 pb-3"
        >
          {TABS.map(({ type, icon: Icon }) => {
            const active = activeTab === type;
            const count = byType[type].length;
            const cls = active
              ? "border-accent/45 bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05),0_0_22px_-10px_var(--color-accent-glow)]"
              : "border-hairline-soft bg-bg-grad-a/40 text-text-2 hover:border-hairline hover:text-text";
            return (
              <button
                key={type}
                ref={(el) => {
                  if (el) tabRefs.current.set(type, el);
                  else tabRefs.current.delete(type);
                }}
                type="button"
                role="tab"
                id={`asset-tab-${type}`}
                aria-selected={active}
                aria-controls="asset-panel"
                tabIndex={active ? 0 : -1}
                onClick={() => setActiveTab(type)}
                onKeyDown={(e) => handleTabKeyDown(e, type)}
                className={`inline-flex items-center gap-2 rounded-[8px] border px-3.5 py-2 text-[12.5px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${cls}`}
              >
                <Icon className={`h-4 w-4 ${active ? "text-accent-2" : "text-text-4"}`} />
                <span className="font-medium">{t(`type.${type}`)}</span>
                <span
                  className={`rounded-full px-1.5 py-0.5 font-mono text-[10px] font-semibold tabular-nums ${
                    active ? "bg-accent-soft text-accent-2" : "bg-bg-grad-b/70 text-text-4"
                  }`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </header>

      <main className="relative mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <div
          role="tabpanel"
          id="asset-panel"
          aria-labelledby={`asset-tab-${activeTab}`}
          tabIndex={0}
          className="rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
        {assets.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-hairline bg-bg-grad-a/30 py-24 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-dim text-accent-2">
              <ActiveIcon className="h-5 w-5" />
            </div>
            <p className="font-editorial text-[20px] leading-tight text-text">{t(EMPTY_KEY[activeTab])}</p>
            <p className="max-w-sm text-[12px] leading-5 text-text-4">{t("library_empty_hint")}</p>
            <button
              type="button"
              onClick={() => setFormModal({ mode: "create" })}
              className={`mt-2 ${ACCENT_BTN_SM_CLS}`}
              style={ACCENT_BUTTON_STYLE}
            >
              <Plus className="h-4 w-4" />
              {t("add_asset")}
            </button>
          </div>
        ) : (
          <AssetGrid
            assets={assets}
            onEdit={handleEditAsset}
            onDelete={handleDeleteAsset}
          />
        )}
        </div>
      </main>

      {formModal && (
        <AssetFormModal
          type={formModal.asset?.type ?? activeTab}
          mode={formModal.mode}
          initialData={formModal.asset}
          previewImageUrl={
            formModal.asset
              ? API.getGlobalAssetUrl(formModal.asset.image_path, formModal.asset.updated_at) ?? undefined
              : undefined
          }
          onClose={() => setFormModal(null)}
          onSubmit={handleSubmit}
        />
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        tone="danger"
        title={
          deleteTarget
            ? t("delete_confirm", { type: t(`type.${deleteTarget.type}`) })
            : ""
        }
        description={deleteTarget ? <span className="font-mono">「{deleteTarget.name}」</span> : null}
        confirmLabel={t("delete")}
        loadingLabel={t("loading")}
        cancelLabel={t("cancel")}
        loading={deleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          if (!deleting) setDeleteTarget(null);
        }}
      />
    </div>
  );
}
