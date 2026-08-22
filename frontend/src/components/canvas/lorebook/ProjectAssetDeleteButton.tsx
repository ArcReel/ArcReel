import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { API, type ProjectAssetType } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";

type CardAssetType = Extract<ProjectAssetType, "character" | "scene" | "prop">;

interface ProjectAssetDeleteButtonProps {
  projectName: string;
  assetType: CardAssetType;
  name: string;
  busy?: boolean;
  onReload?: () => Promise<unknown> | void;
}

/**
 * 三类解析资产卡共用的删除入口。Web 只做确认、占用复核和错误呈现，实际状态变更由
 * DELETE API 与 Agent 共用的 ProjectManager.delete_asset 完成。
 */
export function ProjectAssetDeleteButton({
  projectName,
  assetType,
  name,
  busy = false,
  onReload,
}: ProjectAssetDeleteButtonProps) {
  const { t } = useTranslation(["assets", "common"]);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const rejectIfBusy = () => {
    if (busy || deleting) {
      useAppStore.getState().pushToast(t("assets:delete_project_asset_busy_hint"), "info");
      return true;
    }
    return rejectIfAssetBusy(
      assetType,
      projectName,
      name,
      t,
      "assets:delete_project_asset_busy_hint",
    );
  };

  const requestDelete = () => {
    if (rejectIfBusy()) return;
    setConfirming(true);
  };

  const executeDelete = async () => {
    if (rejectIfBusy()) {
      setConfirming(false);
      return;
    }
    setDeleting(true);
    try {
      await API.deleteProjectAsset(projectName, assetType, name);
    } catch (err) {
      useAppStore.getState().pushToast(
        t("assets:delete_project_asset_failed", { message: errMsg(err) }),
        "error",
      );
      setDeleting(false);
      return;
    }

    useAppStore.getState().pushToast(
      t("assets:delete_project_asset_success", { name }),
      "success",
    );
    setConfirming(false);
    try {
      const refreshed = await onReload?.();
      if (refreshed === false) {
        useAppStore.getState().pushToast(t("assets:delete_project_asset_refresh_failed"), "warning");
      }
    } catch {
      useAppStore.getState().pushToast(t("assets:delete_project_asset_refresh_failed"), "warning");
    } finally {
      setDeleting(false);
    }
  };

  const typeLabel = t(`assets:type.${assetType}`);

  return (
    <>
      <button
        type="button"
        onClick={requestDelete}
        disabled={busy || deleting}
        title={t("assets:delete_project_asset")}
        aria-label={t("assets:delete_project_asset")}
        className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[var(--color-warm-tint-faint)] hover:text-[var(--color-warm)] disabled:cursor-not-allowed disabled:opacity-40"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
      <ConfirmDialog
        open={confirming}
        title={t("assets:delete_project_asset_confirm_title", { name })}
        description={t("assets:delete_project_asset_confirm_description", { type: typeLabel })}
        confirmLabel={t("common:delete")}
        loadingLabel={t("assets:deleting_project_asset")}
        tone="danger"
        loading={deleting}
        onConfirm={executeDelete}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
