import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Search } from "lucide-react";
import { AssetGrid } from "@/components/assets/AssetGrid";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { useAssetsStore } from "@/stores/assets-store";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Asset, AssetType } from "@/types/asset";

const TABS: AssetType[] = ["character", "scene", "prop"];

export function AssetLibraryPage() {
  const { t } = useTranslation("assets");
  const [activeTab, setActiveTab] = useState<AssetType>("character");
  const [q, setQ] = useState("");
  const [formModal, setFormModal] = useState<{ mode: "create" | "edit"; asset?: Asset } | null>(null);

  const byType = useAssetsStore((s) => s.byType);
  const loadList = useAssetsStore((s) => s.loadList);
  const addAsset = useAssetsStore((s) => s.addAsset);
  const updateAsset = useAssetsStore((s) => s.updateAsset);
  const deleteAssetLocal = useAssetsStore((s) => s.deleteAsset);

  useEffect(() => {
    void loadList(activeTab, q || undefined);
  }, [activeTab, q, loadList]);

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

  const handleDelete = async (asset: Asset) => {
    if (!confirm(t("delete_confirm", { type: t(`type.${asset.type}`) }))) return;
    try {
      await deleteAssetLocal(asset.id, asset.type);
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    }
  };

  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-900">
        <h2 className="text-sm font-semibold text-white">{t("library_title")}</h2>
        <div className="flex-1 flex items-center gap-2 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded">
          <Search className="h-3.5 w-3.5 text-gray-500" />
          <input type="text" placeholder={t("search_placeholder")}
            value={q} onChange={(e) => setQ(e.target.value)}
            className="flex-1 bg-transparent text-sm text-gray-200 outline-none" />
        </div>
        <button onClick={() => setFormModal({ mode: "create" })}
          className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-500">
          <Plus className="h-3.5 w-3.5" />
          {t("add_asset")}
        </button>
      </header>

      <nav className="flex border-b border-gray-800 px-4 gap-0">
        {TABS.map((tt) => (
          <button key={tt} type="button"
            onClick={() => setActiveTab(tt)}
            className={`px-4 py-2 text-sm transition-colors ${
              activeTab === tt ? "text-white border-b-2 border-indigo-500" : "text-gray-500 hover:text-gray-300"
            }`}>
            {t(`type.${tt}`)} ({byType[tt].length})
          </button>
        ))}
      </nav>

      <div className="flex-1 overflow-y-auto p-4">
        {assets.length === 0 ? (
          <div className="text-center py-16 text-gray-500 text-sm">{t("no_assets_hint")}</div>
        ) : (
          <AssetGrid assets={assets} onEdit={(a) => setFormModal({ mode: "edit", asset: a })} onDelete={handleDelete} />
        )}
      </div>

      {formModal && (
        <AssetFormModal
          type={formModal.asset?.type ?? activeTab}
          mode={formModal.mode}
          scope="library"
          initialData={formModal.asset}
          onClose={() => setFormModal(null)}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
