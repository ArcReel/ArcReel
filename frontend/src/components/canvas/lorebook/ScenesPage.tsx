import { useTranslation } from "react-i18next";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import type { Scene } from "@/types";
import { AssetGallery } from "./AssetGallery";

interface Props {
  projectName: string;
  scenes: Record<string, Scene>;
  onGenerateScene: (name: string) => void;
  onRestoreSceneVersion?: () => Promise<unknown> | void;
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
  onGenerateScene,
  onRestoreSceneVersion,
  onRefreshProject,
  generatingSceneNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  return (
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
      libraryPreview={libraryPreview}
    />
  );
}
