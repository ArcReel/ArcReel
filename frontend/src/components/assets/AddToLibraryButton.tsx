import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Package } from "lucide-react";
import { API } from "@/api";
import { AssetFormModal } from "./AssetFormModal";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
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
  /** 资源被生成/编辑任务占用：禁用入库，避免把编辑前的旧图复制进全局资产库 */
  busy?: boolean;
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
  busy = false,
}: Props) {
  const { t } = useTranslation("assets");
  const [modal, setModal] = useState<{ conflictWith?: Asset } | null>(null);
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const previewUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, sheetFp) : undefined;

  const openPreview = async () => {
    if (busy) return;
    try {
      const res = await API.listAssets({ type: resourceType, q: resourceId });
      const exact = res.items.find((a) => a.name === resourceId);
      setModal({ conflictWith: exact });
    } catch {
      setModal({});
    }
  };

  const handleSubmit = async (payload: { name: string; description: string; voice_style: string; overwrite?: boolean }) => {
    // 弹窗打开后资源可能通过 SSE / 其他标签页 / Agent 进入生成或 image_edit 占用态，
    // 提交时须复核最新 busy 值，避免把占用期间的旧图复制进全局资产库
    if (busy) {
      useAppStore.getState().pushToast(t("add_to_library_busy_hint"), "error");
      return;
    }
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
      useAppStore.getState().pushToast(errMsg(err), "error");
      throw err;
    }
  };

  const defaultClass = showLabel
    ? "focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
    : "focus-ring inline-flex items-center justify-center h-6 w-6 rounded transition-colors hover:bg-[oklch(1_0_0_/_0.05)]";

  return (
    <>
      <button type="button" onClick={() => void openPreview()}
        disabled={busy}
        aria-label={t("add_to_library")}
        title={busy ? t("add_to_library_busy_hint") : t("add_to_library")}
        className={className ?? defaultClass}
        style={className ? undefined : { color: "var(--color-text-3)" }}>
        <Package className="h-3 w-3" />
        {showLabel && <span>{t("add_to_library_short")}</span>}
      </button>
      {modal && (
        <AssetFormModal
          type={resourceType}
          mode="import"
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
