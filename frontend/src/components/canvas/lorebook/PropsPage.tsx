import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Package } from "lucide-react";
import { GalleryToolbar } from "./GalleryToolbar";
import { PropCard } from "./PropCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useScrollTarget } from "@/hooks/useScrollTarget";
import { errMsg } from "@/utils/async";
import type { ImageModelSelection, Prop } from "@/types";
import { GalleryEmptyState } from "./GalleryEmptyState";
import { ConfirmAssetSheetsButton } from "./ConfirmAssetSheetsButton";

interface Props {
  projectName: string;
  props: Record<string, Prop>;
  onUpdateProp: (name: string, updates: Partial<Prop>) => void;
  onGenerateProp: (name: string, selection?: ImageModelSelection) => void;
  onAddProp: (name: string, description: string) => Promise<void>;
  onRestorePropVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingPropNames?: Set<string>;
  /** 只读展示（引导演示项目）：不渲染新增 / 入库 / 生成 / 上传入口。 */
  readOnly?: boolean;
}

export function PropsPage({ projectName, props, onUpdateProp, onGenerateProp, onAddProp, onRestorePropVersion, onRefreshProject, generatingPropNames, readOnly = false }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  useScrollTarget("prop");

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
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <GalleryToolbar
        title={t("dashboard:props")}
        count={entries.length}
        onAdd={readOnly ? undefined : () => setAdding(true)}
        onPickFromLibrary={readOnly ? undefined : () => setPicking(true)}
        extraActions={readOnly ? undefined : <ConfirmAssetSheetsButton projectName={projectName} onReload={onRefreshProject} />}
      />
      <div className="px-5 py-5">
        {entries.length === 0 ? (
          <GalleryEmptyState
            icon={<Package className="h-6 w-6" />}
            label={t("dashboard:props")}
            hint={t(readOnly ? "dashboard:no_props_hint" : "dashboard:no_props_hint_clickable")}
            onClick={readOnly ? undefined : () => setAdding(true)}
          />
        ) : (
          <div className="grid justify-evenly gap-4 [grid-template-columns:repeat(auto-fill,320px)]">
            {entries.map(([name, prop]) => (
              <PropCard key={name} name={name} prop={prop} projectName={projectName}
                onUpdate={onUpdateProp}
                onGenerate={onGenerateProp}
                onRestoreVersion={onRestorePropVersion}
                onReload={onRefreshProject}
                generating={generatingPropNames?.has(name)}
                readOnly={readOnly}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AssetFormModal
          type="prop"
          mode="create"
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
