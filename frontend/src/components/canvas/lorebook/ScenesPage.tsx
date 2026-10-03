import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Landmark } from "lucide-react";
import { AssetSheetBatchControls } from "./AssetSheetBatchControls";
import { GalleryToolbar } from "./GalleryToolbar";
import { matchesSheetFilter, useAssetSheetStatus, useSheetStatusByName, type SheetStatusFilter } from "./useAssetSheetStatus";
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
  onRefreshProject?: () => Promise<unknown> | void;
  generatingSceneNames?: Set<string>;
  /** 只读展示（引导演示项目）：不渲染新增 / 入库 / 生成 / 上传入口。 */
  readOnly?: boolean;
}

export function ScenesPage({ projectName, scenes, onUpdateScene, onGenerateScene, onAddScene, onRestoreSceneVersion, onRefreshProject, generatingSceneNames, readOnly = false }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  useScrollTarget("scene");
  const sheetRows = useAssetSheetStatus(projectName);
  const sheetStatus = useSheetStatusByName(sheetRows, "scene");
  const [sheetFilter, setSheetFilter] = useState<SheetStatusFilter>("all");

  const entries = Object.entries(scenes);
  const shownEntries = entries.filter(([name]) => matchesSheetFilter(sheetStatus.get(name), sheetFilter));

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
        onAdd={readOnly ? undefined : () => setAdding(true)}
        onPickFromLibrary={readOnly ? undefined : () => setPicking(true)}
      >
        <AssetSheetBatchControls
          projectName={projectName}
          assetType="scene"
          rows={sheetRows}
          filter={sheetFilter}
          onFilterChange={setSheetFilter}
          readOnly={readOnly}
        />
      </GalleryToolbar>
      <div className="px-5 py-5">
        {entries.length === 0 ? (
          <GalleryEmptyState
            icon={<Landmark className="h-6 w-6" />}
            label={t("dashboard:scenes")}
            hint={t(readOnly ? "dashboard:no_scenes_hint" : "dashboard:no_scenes_hint_clickable")}
            onClick={readOnly ? undefined : () => setAdding(true)}
          />
        ) : (
          <div className="grid justify-evenly gap-4 [grid-template-columns:repeat(auto-fill,320px)]">
            {shownEntries.map(([name, scene]) => (
              <SceneCard key={name} name={name} scene={scene} projectName={projectName}
                onUpdate={onUpdateScene}
                onGenerate={onGenerateScene}
                onRestoreVersion={onRestoreSceneVersion}
                onReload={onRefreshProject}
                generating={generatingSceneNames?.has(name)}
                sheetStatus={sheetStatus.get(name)}
                readOnly={readOnly}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AssetFormModal
          type="scene"
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
