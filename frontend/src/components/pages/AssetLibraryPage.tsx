import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ChevronLeft, Landmark, Package as PackageIcon, Plus, Search, User } from "lucide-react";
import { AssetGrid } from "@/components/assets/AssetGrid";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { useAssetsStore } from "@/stores/assets-store";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { Asset, AssetType } from "@/types/asset";

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

export function AssetLibraryPage() {
  const { t } = useTranslation("assets");
  const [, navigate] = useLocation();
  const [activeTab, setActiveTab] = useState<AssetType>("character");
  const [q, setQ] = useState("");
  const debouncedQ = useDebouncedValue(q, 250);
  const [formModal, setFormModal] = useState<{ mode: "create" | "edit"; asset?: Asset } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Asset | null>(null);

  const byType = useAssetsStore((s) => s.byType);
  const loadList = useAssetsStore((s) => s.loadList);
  const addAsset = useAssetsStore((s) => s.addAsset);
  const updateAsset = useAssetsStore((s) => s.updateAsset);
  const deleteAssetLocal = useAssetsStore((s) => s.deleteAsset);

  useEffect(() => {
    void loadList(activeTab, debouncedQ || undefined);
  }, [activeTab, debouncedQ, loadList]);

  const assets = byType[activeTab];

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
          updateAsset(after);
        } else {
          updateAsset(asset);
        }
      } else {
        const { asset } = await API.createAsset({
          type: activeTab, name: payload.name, description: payload.description,
          voice_style: payload.voice_style, image: payload.image ?? undefined,
        });
        addAsset(asset);
      }
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const asset = deleteTarget;
    setDeleteTarget(null);
    try {
      await deleteAssetLocal(asset.id, asset.type);
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    }
  };

  return (
    <div className="relative flex min-h-screen flex-col bg-gray-950 text-gray-100">
      {/* Decorative ambient glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-[radial-gradient(circle_at_30%_0%,rgba(99,102,241,0.12),transparent_60%)]"
      />

      {/* ---- Page header ---- */}
      <header className="relative border-b border-gray-800/80 bg-gray-950/60 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-start justify-between gap-6 px-6 py-6">
          <div className="flex items-start gap-3">
            <button
              type="button"
              onClick={() => navigate("/app/projects")}
              aria-label={t("back_to_projects")}
              title={t("back_to_projects")}
              className="mt-1 rounded-full border border-gray-800 bg-gray-900/60 p-1.5 text-gray-400 transition-colors hover:border-indigo-500/40 hover:text-indigo-200"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-white">
                {t("library_title")}
              </h1>
              <p className="mt-1 text-sm text-gray-500">{t("library_subtitle")}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-1.5 transition-colors focus-within:border-indigo-500/50">
              <Search className="h-3.5 w-3.5 text-gray-500" />
              <input
                type="text"
                placeholder={t("search_placeholder")}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                className="w-44 bg-transparent text-sm text-gray-200 outline-none placeholder:text-gray-600"
              />
            </div>
            <button
              onClick={() => setFormModal({ mode: "create" })}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-lg shadow-indigo-900/40 transition-all hover:bg-indigo-500 hover:shadow-indigo-700/50"
            >
              <Plus className="h-4 w-4" />
              {t("add_asset")}
            </button>
          </div>
        </div>

        {/* Tabs */}
        <nav className="mx-auto flex max-w-6xl items-center gap-1 px-6">
          {TABS.map(({ type, icon: Icon }) => {
            const active = activeTab === type;
            const count = byType[type].length;
            return (
              <button
                key={type}
                type="button"
                onClick={() => setActiveTab(type)}
                className={`relative flex items-center gap-2 px-4 py-2.5 text-sm transition-colors ${
                  active ? "text-white" : "text-gray-500 hover:text-gray-300"
                }`}
              >
                <Icon className={`h-4 w-4 ${active ? "text-indigo-400" : ""}`} />
                <span className="font-medium">{t(`type.${type}`)}</span>
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${
                    active
                      ? "bg-indigo-500/20 text-indigo-200"
                      : "bg-gray-800 text-gray-500"
                  }`}
                >
                  {count}
                </span>
                {active && (
                  <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-indigo-400" />
                )}
              </button>
            );
          })}
        </nav>
      </header>

      {/* ---- Main content ---- */}
      <main className="relative mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        {assets.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-gray-800 bg-gray-900/30 py-24 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-900 text-gray-500">
              {activeTab === "character" && <User className="h-5 w-5" />}
              {activeTab === "scene" && <Landmark className="h-5 w-5" />}
              {activeTab === "prop" && <PackageIcon className="h-5 w-5" />}
            </div>
            <p className="text-sm font-medium text-gray-300">{t(EMPTY_KEY[activeTab])}</p>
            <p className="max-w-sm text-xs leading-5 text-gray-600">{t("library_empty_hint")}</p>
            <button
              onClick={() => setFormModal({ mode: "create" })}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-lg shadow-indigo-900/40 transition-all hover:bg-indigo-500"
            >
              <Plus className="h-4 w-4" />
              {t("add_asset")}
            </button>
          </div>
        ) : (
          <AssetGrid
            assets={assets}
            onEdit={(a) => setFormModal({ mode: "edit", asset: a })}
            onDelete={(a) => setDeleteTarget(a)}
          />
        )}
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

      {deleteTarget && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t("delete_confirm", { type: t(`type.${deleteTarget.type}`) })}
          onClick={() => setDeleteTarget(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-[420px] max-w-[96vw] rounded-xl border border-gray-700 bg-gray-900 p-5 shadow-2xl"
          >
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 shrink-0 text-amber-400" />
              <div>
                <h3 className="text-sm font-semibold text-white">
                  {t("delete_confirm", { type: t(`type.${deleteTarget.type}`) })}
                </h3>
                <p className="mt-1 text-xs text-gray-400">「{deleteTarget.name}」</p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-lg border border-gray-800 px-4 py-1.5 text-sm text-gray-300 hover:border-gray-600 hover:text-white"
              >
                {t("cancel")}
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                className="rounded-lg bg-red-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg shadow-red-900/40 hover:bg-red-500"
              >
                {t("delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
