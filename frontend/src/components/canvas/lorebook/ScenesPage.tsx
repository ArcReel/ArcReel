import { useState } from "react";
import { useTranslation } from "react-i18next";
import { GalleryToolbar } from "./GalleryToolbar";
import { SceneCard } from "./SceneCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Scene } from "@/types";

interface Props {
  projectName: string;
  scenes: Record<string, Scene>;
  onUpdateScene: (name: string, updates: Partial<Scene>) => void;
  onGenerateScene: (name: string) => void;
  onAddScene: (name: string, description: string) => Promise<void>;
  onRestoreSceneVersion?: () => Promise<void> | void;
  generatingSceneNames?: Set<string>;
}

export function ScenesPage({ projectName, scenes, onUpdateScene, onGenerateScene, onAddScene, onRestoreSceneVersion, generatingSceneNames }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  const entries = Object.entries(scenes);

  const handleImport = async (ids: string[]) => {
    try {
      await API.applyAssetsToProject({
        asset_ids: ids,
        target_project: projectName,
        conflict_policy: "skip",
      });
      useAppStore.getState().pushToast(t("assets:import_count", { count: ids.length }), "success");
      window.location.reload();
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex flex-col">
      <GalleryToolbar
        title={t("dashboard:scenes")}
        count={entries.length}
        onAdd={() => setAdding(true)}
        onPickFromLibrary={() => setPicking(true)}
      />
      <div className="p-4">
        {entries.length === 0 ? (
          <div className="py-16 text-center text-gray-500 text-sm">
            {t("dashboard:no_scenes_hint_clickable")}
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {entries.map(([name, scene]) => (
              <SceneCard key={name} name={name} scene={scene} projectName={projectName}
                onUpdate={onUpdateScene}
                onGenerate={onGenerateScene}
                onRestoreVersion={onRestoreSceneVersion}
                generating={generatingSceneNames?.has(name)}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AssetFormModal
          type="scene"
          mode="create"
          scope="project"
          targetProject={projectName}
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description }) => {
            await onAddScene(name, description);
            setAdding(false);
          }}
        />
      )}

      {picking && (
        <AssetPickerModal
          type="scene"
          existingNames={new Set(Object.keys(scenes))}
          onClose={() => setPicking(false)}
          onImport={(ids) => { void handleImport(ids); }}
        />
      )}
    </div>
  );
}
