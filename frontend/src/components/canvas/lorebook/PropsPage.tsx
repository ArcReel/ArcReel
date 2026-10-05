import { useTranslation } from "react-i18next";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import type { Prop } from "@/types";
import { AssetGallery } from "./AssetGallery";

interface Props {
  projectName: string;
  props: Record<string, Prop>;
  onGenerateProp: (name: string) => void;
  onRestorePropVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingPropNames?: Set<string>;
  /** 只读展示（引导演示项目）：不渲染新增、入库、生成、上传入口。 */
  readOnly?: boolean;
}

const libraryPreview = (prop: Prop): LibraryImportPreview => ({
  description: prop.description,
  sheetPath: prop.prop_sheet,
});

export function PropsPage({
  projectName,
  props,
  onGenerateProp,
  onRestorePropVersion,
  onRefreshProject,
  generatingPropNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  return (
    <AssetGallery
      projectName={projectName}
      assetType="prop"
      title={t("props")}
      assets={props}
      generatingNames={generatingPropNames}
      readOnly={readOnly}
      onGenerate={onGenerateProp}
      onRestoreVersion={onRestorePropVersion}
      onReload={onRefreshProject}
      libraryPreview={libraryPreview}
    />
  );
}
