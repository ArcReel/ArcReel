import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import type { Prop } from "@/types";
import { AssetGallery } from "./AssetGallery";
import { PropCard } from "./PropCard";

interface Props {
  projectName: string;
  props: Record<string, Prop>;
  onUpdateProp: (name: string, updates: Partial<Prop>) => void;
  onGenerateProp: (name: string) => void;
  onAddProp: (name: string, description: string) => Promise<void>;
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
  onUpdateProp,
  onGenerateProp,
  onAddProp,
  onRestorePropVersion,
  onRefreshProject,
  generatingPropNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [adding, setAdding] = useState(false);

  return (
    <>
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
        onAdd={() => setAdding(true)}
        libraryPreview={libraryPreview}
        renderEditor={(name, { sheetStatus, generating }) =>
          props[name] ? (
            <PropCard
              name={name}
              prop={props[name]}
              projectName={projectName}
              onUpdate={onUpdateProp}
              onGenerate={onGenerateProp}
              generating={generating}
              sheetStatus={sheetStatus}
              readOnly={readOnly}
            />
          ) : null
        }
      />

      {adding && !readOnly && (
        <AssetFormModal
          type="prop"
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description }) => {
            await onAddProp(name, description);
            setAdding(false);
          }}
        />
      )}
    </>
  );
}
