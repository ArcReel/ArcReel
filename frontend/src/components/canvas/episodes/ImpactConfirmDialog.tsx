import { useState, type ReactNode } from "react";
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

export interface ImpactConfirmRequest {
  title: string;
  /** Body, usually a server-written affected-episode list. */
  body: ReactNode;
  confirmLabel: string;
  /** Submitting button label, default `confirmLabel`. */
  runningLabel?: string;
  /** Artifact loss or irreversibility makes the confirmation destructive. */
  destructive: boolean;
  /** Body has focusable elements such as inputs; exclude the body itself from Tab order. */
  interactiveBody?: boolean;
}

/**
 * Episodes-view confirmation before episode/source changes: delete, manual split, whole-source changes, replanning/adoption, etc.
 * Null `request` closes; submissions disable both buttons and ignore closing. Long affected lists support keyboard scrolling.
 */
export function ImpactConfirmDialog({
  request,
  busy,
  onConfirm,
  onCancel,
}: {
  request: ImpactConfirmRequest | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("common");
  // Retain prior content during closing animation.
  const [shown, setShown] = useState(request);
  if (request !== null && request !== shown) setShown(request);

  return (
    <AlertDialog
      open={request !== null}
      onOpenChange={(next) => {
        if (!next && !busy) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{shown?.title}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody
          {...(shown?.interactiveBody ? {} : { tabIndex: 0, role: "region", "aria-label": shown?.title })}
        >
          <AlertDialogDescription render={<div />}>{shown?.body}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant={shown?.destructive ? "destructive" : "default"}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {busy ? (shown?.runningLabel ?? shown?.confirmLabel) : shown?.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** Server-written confirmation list: preserve newlines and explain changes occurring during confirmation first. */
export function ImpactText({ text, changedNotice }: { text: string; changedNotice?: string | null }) {
  return (
    <div className="flex flex-col gap-2">
      {changedNotice ? <p className="text-warn">{changedNotice}</p> : null}
      <p className="whitespace-pre-line">{text}</p>
    </div>
  );
}
