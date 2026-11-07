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

/** Permanently delete a library asset; copies already applied to projects are unaffected. */
export function DeleteAssetDialog({
  asset,
  onClose,
  onDeleted,
}: {
  /** null closes the dialog. */
  asset: Asset | null;
  onClose: () => void;
  onDeleted: (asset: Asset) => void;
}) {
  const { t } = useTranslation("assets");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Retain the last asset during closing animation; clear previous errors on every opening, including reopening the same asset
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
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
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
