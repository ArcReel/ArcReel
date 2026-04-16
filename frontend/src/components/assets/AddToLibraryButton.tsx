import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Package } from "lucide-react";
import { API } from "@/api";
import { AssetFormModal } from "./AssetFormModal";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { Asset, AssetType } from "@/types/asset";

interface Props {
  resourceType: AssetType;
  resourceId: string;
  projectName: string;
  initialDescription: string;
  initialVoiceStyle?: string;
  sheetPath?: string | null;
  className?: string;
  showLabel?: boolean;
}

export function AddToLibraryButton({
  resourceType,
  resourceId,
  projectName,
  initialDescription,
  initialVoiceStyle = "",
  sheetPath = null,
  className,
  showLabel = false,
}: Props) {
  const { t } = useTranslation("assets");
  const [modal, setModal] = useState<{ conflictWith?: Asset } | null>(null);
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const previewUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, sheetFp) : undefined;

  const openPreview = async () => {
    try {
      const res = await API.listAssets({ type: resourceType, q: resourceId });
      const exact = res.items.find((a) => a.name === resourceId);
      setModal({ conflictWith: exact });
    } catch {
      setModal({});
    }
  };

  const handleSubmit = async (payload: { name: string; description: string; voice_style: string; overwrite?: boolean }) => {
    try {
      await API.addAssetFromProject({
        project_name: projectName,
        resource_type: resourceType,
        resource_id: resourceId,
        override_name: payload.name !== resourceId ? payload.name : undefined,
        overwrite: payload.overwrite,
      });
      useAppStore.getState().pushToast(t("add_to_library_success", { name: payload.name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
      throw err;
    }
  };

  const defaultClass = showLabel
    ? "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
    : "inline-flex items-center justify-center h-6 w-6 rounded text-gray-500 hover:bg-gray-800 hover:text-indigo-300 transition-colors";

  return (
    <>
      <button type="button" onClick={() => void openPreview()}
        aria-label={t("add_to_library")}
        title={t("add_to_library")}
        className={className ?? defaultClass}>
        <Package className="h-3 w-3" />
        {showLabel && <span>{t("add_to_library_short")}</span>}
      </button>
      {modal && (
        <AssetFormModal
          type={resourceType}
          mode="import"
          scope="library"
          initialData={{
            name: resourceId,
            description: initialDescription,
            voice_style: initialVoiceStyle,
          }}
          previewImageUrl={previewUrl}
          conflictWith={modal.conflictWith}
          onClose={() => setModal(null)}
          onSubmit={handleSubmit}
        />
      )}
    </>
  );
}
