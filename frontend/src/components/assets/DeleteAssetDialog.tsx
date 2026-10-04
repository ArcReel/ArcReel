import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { errMsg } from "@/utils/async";
import type { Asset } from "@/types/asset";

/** 从资产库删除一个资产。删除不可恢复，已应用到项目里的副本不受影响。 */
export function DeleteAssetDialog({
  asset,
  onClose,
  onDeleted,
}: {
  /** 为 null 时关闭。 */
  asset: Asset | null;
  onClose: () => void;
  onDeleted: (asset: Asset) => void;
}) {
  const { t } = useTranslation("assets");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 关闭动画期间沿用上一次的资产；每次打开（含再次打开同一个资产）都清掉上一次的错误
  const [prev, setPrev] = useState(asset);
  const [shown, setShown] = useState(asset);
  if (asset !== prev) {
    setPrev(asset);
    if (asset) {
      setShown(asset);
      setError(null);
    }
  }

  const handleDelete = async () => {
    if (!shown || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await API.deleteAsset(shown.id);
      onDeleted(shown);
      onClose();
    } catch (err) {
      setError(t("delete_failed", { message: errMsg(err) }));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AlertDialog
      open={asset !== null}
      onOpenChange={(next) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!next && !deleting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("delete_title", { type: shown ? t(`type.${shown.type}`) : "", name: shown?.name ?? "" })}
          </AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("delete_title", { type: shown ? t(`type.${shown.type}`) : "", name: shown?.name ?? "" })}>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>
              {t(shown?.audio_path ? "delete_description_with_audio" : "delete_description")}
            </AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void handleDelete()}>
            {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("delete")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
