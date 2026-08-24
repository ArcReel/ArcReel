import { useState } from "react";
import { useTranslation } from "react-i18next";
import { BadgeCheck, Loader2 } from "lucide-react";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

interface ConfirmAssetSheetsButtonProps {
  projectName: string;
  onReload?: () => Promise<unknown> | void;
}

/**
 * Web counterpart to the CrocoTV `confirm_asset_sheets` tool.  Both routes
 * delegate to the same service and only update Artifact Manifest currency.
 */
export function ConfirmAssetSheetsButton({ projectName, onReload }: ConfirmAssetSheetsButtonProps) {
  const { t } = useTranslation(["assets"]);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);

  const execute = async () => {
    setSaving(true);
    try {
      const result = await API.confirmCurrentAssetSheets(projectName);
      useAppStore.getState().pushToast(
        t("assets:confirm_current_success", { count: result.confirmed_count }),
        "success",
      );
      setConfirming(false);
      await onReload?.();
    } catch (error) {
      useAppStore.getState().pushToast(
        t("assets:confirm_current_failed", { message: errMsg(error) }),
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        disabled={saving}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          color: "var(--color-text-2)",
          border: "1px solid var(--color-hairline)",
          background: "oklch(0.22 0.011 265 / 0.5)",
        }}
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" /> : <BadgeCheck className="h-3.5 w-3.5" />}
        {t("assets:confirm_current_action")}
      </button>
      <ConfirmDialog
        open={confirming}
        title={t("assets:confirm_current_title")}
        description={t("assets:confirm_current_description")}
        confirmLabel={t("assets:confirm_current_confirm")}
        loadingLabel={t("assets:confirming_current")}
        loading={saving}
        onConfirm={execute}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
