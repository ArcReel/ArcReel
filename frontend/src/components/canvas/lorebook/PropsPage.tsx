import { useState } from "react";
import { useTranslation } from "react-i18next";
import { GalleryToolbar } from "./GalleryToolbar";
import { PropCard } from "./PropCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Prop } from "@/types";

interface Props {
  projectName: string;
  props: Record<string, Prop>;
  onUpdateProp: (name: string, updates: Partial<Prop>) => void;
  onGenerateProp: (name: string) => void;
  onAddProp: (name: string, description: string) => Promise<void>;
  onRestorePropVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<void> | void;
  generatingPropNames?: Set<string>;
}

export function PropsPage({ projectName, props, onUpdateProp, onGenerateProp, onAddProp, onRestorePropVersion, onRefreshProject, generatingPropNames }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  const entries = Object.entries(props);

  const handleImport = async (ids: string[]) => {
    try {
      await API.applyAssetsToProject({
        asset_ids: ids,
        target_project: projectName,
        conflict_policy: "skip",
      });
      useAppStore.getState().pushToast(t("assets:import_count", { count: ids.length }), "success");
      await onRefreshProject?.();
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex flex-col">
      <GalleryToolbar
        title={t("dashboard:props")}
        count={entries.length}
        onAdd={() => setAdding(true)}
        onPickFromLibrary={() => setPicking(true)}
      />
      <div className="p-4">
        {entries.length === 0 ? (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="w-full rounded-lg border border-dashed border-gray-700 py-16 text-center text-sm text-gray-500 transition-colors hover:border-indigo-500/60 hover:bg-gray-900/50 hover:text-gray-300 focus-ring"
          >
            {t("dashboard:no_props_hint_clickable")}
          </button>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {entries.map(([name, prop]) => (
              <PropCard key={name} name={name} prop={prop} projectName={projectName}
                onUpdate={onUpdateProp}
                onGenerate={onGenerateProp}
                onRestoreVersion={onRestorePropVersion}
                onReload={onRefreshProject}
                generating={generatingPropNames?.has(name)}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AssetFormModal
          type="prop"
          mode="create"
          scope="project"
          targetProject={projectName}
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description }) => {
            await onAddProp(name, description);
            setAdding(false);
          }}
        />
      )}

      {picking && (
        <AssetPickerModal
          type="prop"
          existingNames={new Set(Object.keys(props))}
          onClose={() => setPicking(false)}
          onImport={(ids) => { void handleImport(ids); }}
        />
      )}
    </div>
  );
}
