import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Landmark } from "lucide-react";
import { GalleryToolbar } from "./GalleryToolbar";
import { SceneCard } from "./SceneCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { errMsg } from "@/utils/async";
import type { Scene } from "@/types";
import { GalleryEmptyState } from "./GalleryEmptyState";

interface Props {
  projectName: string;
  scenes: Record<string, Scene>;
  onUpdateScene: (name: string, updates: Partial<Scene>) => void;
  onGenerateScene: (name: string) => void;
  onAddScene: (name: string, description: string) => Promise<void>;
  onRestoreSceneVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<void> | void;
  generatingSceneNames?: Set<string>;
}

export function ScenesPage({ projectName, scenes, onUpdateScene, onGenerateScene, onAddScene, onRestoreSceneVersion, onRefreshProject, generatingSceneNames }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  useScrollTarget("scene");

  const entries = Object.entries(scenes);

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
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <GalleryToolbar
        title={t("dashboard:scenes")}
        count={entries.length}
        onAdd={() => setAdding(true)}
        onPickFromLibrary={() => setPicking(true)}
      />
      <div className="px-5 py-5">
        {entries.length === 0 ? (
          <GalleryEmptyState
            icon={<Landmark className="h-6 w-6" />}
            label={t("dashboard:scenes")}
            hint={t("dashboard:no_scenes_hint_clickable")}
            onClick={() => setAdding(true)}
          />
        ) : (
          <div className="grid justify-evenly gap-4 [grid-template-columns:repeat(auto-fill,320px)]">
            {entries.map(([name, scene]) => (
              <SceneCard key={name} name={name} scene={scene} projectName={projectName}
                onUpdate={onUpdateScene}
                onGenerate={onGenerateScene}
                onRestoreVersion={onRestoreSceneVersion}
                onReload={onRefreshProject}
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
