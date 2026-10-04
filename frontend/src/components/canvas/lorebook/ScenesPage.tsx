import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import type { Scene } from "@/types";
import { AssetGallery } from "./AssetGallery";
import { SceneCard } from "./SceneCard";

interface Props {
  projectName: string;
  scenes: Record<string, Scene>;
  onUpdateScene: (name: string, updates: Partial<Scene>) => void;
  onGenerateScene: (name: string) => void;
  onAddScene: (name: string, description: string) => Promise<void>;
  onRestoreSceneVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingSceneNames?: Set<string>;
  /** 只读展示（引导演示项目）：不渲染新增、入库、生成、上传入口。 */
  readOnly?: boolean;
}

const libraryPreview = (scene: Scene): LibraryImportPreview => ({
  description: scene.description,
  sheetPath: scene.scene_sheet,
});

export function ScenesPage({
  projectName,
  scenes,
  onUpdateScene,
  onGenerateScene,
  onAddScene,
  onRestoreSceneVersion,
  onRefreshProject,
  generatingSceneNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [adding, setAdding] = useState(false);

  return (
    <>
      <AssetGallery
        projectName={projectName}
        assetType="scene"
        title={t("scenes")}
        assets={scenes}
        generatingNames={generatingSceneNames}
        readOnly={readOnly}
        onGenerate={onGenerateScene}
        onRestoreVersion={onRestoreSceneVersion}
        onReload={onRefreshProject}
        onAdd={() => setAdding(true)}
        libraryPreview={libraryPreview}
        renderEditor={(name, { sheetStatus, generating }) =>
          scenes[name] ? (
            <SceneCard
              name={name}
              scene={scenes[name]}
              projectName={projectName}
              onUpdate={onUpdateScene}
              onGenerate={onGenerateScene}
              generating={generating}
              sheetStatus={sheetStatus}
              readOnly={readOnly}
            />
          ) : null
        }
      />

      {adding && !readOnly && (
        <AssetFormModal
          type="scene"
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description }) => {
            await onAddScene(name, description);
            setAdding(false);
          }}
        />
      )}
    </>
  );
}
