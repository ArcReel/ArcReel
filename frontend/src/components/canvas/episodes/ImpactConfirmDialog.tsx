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
  body: ReactNode;
  confirmLabel: string;
  runningLabel?: string;
  destructive: boolean;
  interactiveBody?: boolean;
}

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

export function ImpactText({ text, changedNotice }: { text: string; changedNotice?: string | null }) {
  return (
    <div className="flex flex-col gap-2">
      {changedNotice ? <p className="text-warn">{changedNotice}</p> : null}
      <p className="whitespace-pre-line">{text}</p>
    </div>
  );
}
