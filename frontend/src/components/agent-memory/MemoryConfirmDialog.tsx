import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

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

export interface MemoryConfirmRequest {
  title: string;
  description: string;
  confirmLabel: string;
  /** Throwing means failure: keep the dialog open and show the error. */
  run: () => Promise<void>;
}

/** Confirmation for deleting memory files or clearing memory directories; both are irreversible. null `request` closes it. */
export function MemoryConfirmDialog({
  request,
  onClose,
}: {
  request: MemoryConfirmRequest | null;
  onClose: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Retain the last content during closing animation; clear the previous error when the request changes
  const [shown, setShown] = useState(request);
  if (request !== null && request !== shown) {
    setShown(request);
    setError(null);
  }

  const confirm = async () => {
    if (!shown || running) return;
    setRunning(true);
    setError(null);
    try {
      await shown.run();
      onClose();
    } catch (err) {
      setError(t("agent_memory_action_failed", { message: errMsg(err) }));
    } finally {
      setRunning(false);
    }
  };

  return (
    <AlertDialog
      open={request !== null}
      onOpenChange={(next) => {
        // Ignore Escape during submission so the dialog cannot disappear before the request completes
        if (!next && !running) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{shown?.title}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={shown?.title}>
          <div className="flex flex-col gap-3">
            {/* The explanation has two paragraphs; render the description element as div to avoid nesting paragraphs inside p */}
            <AlertDialogDescription render={<div />}>
              <div className="flex flex-col gap-2">
                <p>{shown?.description}</p>
                <p>{t("agent_memory_session_notice")}</p>
              </div>
            </AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={running}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={running} onClick={() => void confirm()}>
            {running && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {shown?.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
