import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FilePlus2, Loader2 } from "lucide-react";
import { cn } from "cn";
import { API } from "@/api";
import { AdScriptButton, AdScriptInputsLink, AdScriptProgress } from "@/components/canvas/shared/AdScriptDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAdScriptEntry } from "@/hooks/useAdScriptEntry";
import { useAppStore } from "@/stores/app-store";
import { refreshAfterWrite } from "./refreshAfterWrite";
import { errMsg } from "@/utils/async";

interface Props {
  projectName: string;
  episode: number;
  /** Unconfirmed planning or repairable drafts exist; confirm discarding before creating a formal script. */
  discardsPlan: boolean;
  variant?: "outline" | "ghost";
  /** Layout only, such as `ml-auto`. */
  className?: string;
}

/**
 * Start blank creates an empty formal script when none exists, then users add storyboards individually in the timeline.
 * Refresh after success; the episode page switches to storyboards once the script appears. Confirm discarded planning with AlertDialog first.
 */
export function StartBlankScriptButton({ projectName, episode, discardsPlan, variant = "outline", className }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const start = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await API.startBlankScript(projectName, episode);
      setConfirmOpen(false);
      await refreshAfterWrite(projectName, t);
    } catch (err) {
      useAppStore.getState().pushToast(t("blank_script_failed", { message: errMsg(err) }), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant={variant}
              size="sm"
              className={className}
              disabled={submitting}
              onClick={() => (discardsPlan ? setConfirmOpen(true) : void start())}
            />
          }
        >
          {submitting && !discardsPlan ? (
            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
          ) : (
            <FilePlus2 aria-hidden data-icon="inline-start" />
          )}
          {t("blank_script_start")}
        </TooltipTrigger>
        <TooltipContent>{t("blank_script_hint")}</TooltipContent>
      </Tooltip>
      {discardsPlan && (
        <AlertDialog
          open={confirmOpen}
          onOpenChange={(next) => {
            if (!next && !submitting) setConfirmOpen(false);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("blank_script_discard_title")}</AlertDialogTitle>
              <AlertDialogDescription>{t("blank_script_discard_desc")}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void start()}>
                {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                {t("blank_script_start")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </>
  );
}

/**
 * Canvas for episodes without script-planning support, such as ads/short films, and no formal script: explain and offer AI generate script beside Start blank.
 * Disable AI generation when both creative inspiration and products are absent, linking to their input area.
 */
export function NoScriptBlankState({ projectName, episode, className }: { projectName: string; episode: number; className?: string }) {
  const { t } = useTranslation("dashboard");
  const { refusedReason } = useAdScriptEntry(projectName, episode);
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 text-muted-foreground", className)}>
      <p>{t("timeline_no_script_blank_hint")}</p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <AdScriptButton projectName={projectName} episode={episode} regenerate={false} prominent />
        <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan={false} />
      </div>
      {refusedReason && (
        <p className="text-sm">
          {refusedReason} <AdScriptInputsLink className="text-primary" />
        </p>
      )}
      <AdScriptProgress projectName={projectName} episode={episode} noScript className="w-full max-w-md" />
    </div>
  );
}
