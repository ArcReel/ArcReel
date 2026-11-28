import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { FilePlus2, Loader2 } from "lucide-react";
import { cn } from "cn";
import { EPISODE_PLANNING_SLOTS, promptAuthoringResourceId, scriptPlanResourceId } from "@/actions/generation";
import { API } from "@/api";
import { AdScriptButton, AdScriptInputsLink, AdScriptProgress } from "@/components/canvas/shared/AdScriptDialog";
import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
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
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAdScriptEntry } from "@/hooks/useAdScriptEntry";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { refreshAfterWrite } from "./refreshAfterWrite";
import { errMsg } from "@/utils/async";

type BusyReasonKey = "script_plan_busy" | "episode_planning_busy" | "ad_script_busy";

function busyReasonFromActive(
  episode: number,
  activeEpisodePlans: ReadonlySet<string>,
  activeScriptPlans: ReadonlySet<string>,
  activeEpisodeScripts: ReadonlySet<string>,
): BusyReasonKey | null {
  if (activeScriptPlans.has(scriptPlanResourceId(episode))) return "script_plan_busy";
  if (EPISODE_PLANNING_SLOTS.some((slot) => activeEpisodePlans.has(slot))) return "episode_planning_busy";
  if (activeEpisodeScripts.has(promptAuthoringResourceId(episode))) return "ad_script_busy";
  return null;
}

function latestBusyReasonKey(projectName: string, episode: number): BusyReasonKey | null {
  if (isResourceBusy("text_script_plan", projectName, scriptPlanResourceId(episode))) return "script_plan_busy";
  if (EPISODE_PLANNING_SLOTS.some((slot) => isResourceBusy("text_episode_plan", projectName, slot))) {
    return "episode_planning_busy";
  }
  if (isResourceBusy("text_episode_script", projectName, promptAuthoringResourceId(episode))) {
    return "ad_script_busy";
  }
  return null;
}

interface Props {
  projectName: string;
  episode: number;
  discardsPlan: boolean;
  disabled?: boolean;
  variant?: "outline" | "ghost";
  className?: string;
}

export function StartBlankScriptButton({
  projectName,
  episode,
  discardsPlan,
  disabled = false,
  variant = "outline",
  className,
}: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const confirmLeave = useConfirmLeave();
  const reasonId = useId();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const activeEpisodePlans = useActiveResourceIds("text_episode_plan", projectName);
  const activeScriptPlans = useActiveResourceIds("text_script_plan", projectName);
  const activeEpisodeScripts = useActiveResourceIds("text_episode_script", projectName);
  const busyReasonKey = busyReasonFromActive(episode, activeEpisodePlans, activeScriptPlans, activeEpisodeScripts);
  const busyReason = busyReasonKey ? t(busyReasonKey) : null;
  const entryDisabled = submitting || disabled || busyReason !== null;

  const start = async (): Promise<boolean> => {
    if (submitting || disabled) return false;
    const latestBusy = latestBusyReasonKey(projectName, episode);
    if (latestBusy !== null) {
      useAppStore.getState().pushToast(t(latestBusy), "error");
      return false;
    }
    setSubmitting(true);
    try {
      await API.startBlankScript(projectName, episode);
      setConfirmOpen(false);
      await refreshAfterWrite(projectName, t, { scope: { episodes: [episode] } });
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(t("blank_script_failed", { message: errMsg(err) }), "error");
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpenOrStart = () => {
    if (entryDisabled) return;
    const latestBusy = latestBusyReasonKey(projectName, episode);
    if (latestBusy !== null) {
      useAppStore.getState().pushToast(t(latestBusy), "error");
      return;
    }
    if (discardsPlan) {
      setConfirmOpen(true);
    } else {
      confirmLeave(start);
    }
  };

  const discardTitle = t("blank_script_discard_title");

  return (
    <>
      <Tooltip>
        <TooltipTrigger render={<span className={cn("inline-flex", className)} />}>
          <Button
            variant={variant}
            size="sm"
            disabled={entryDisabled}
            aria-describedby={busyReason !== null ? reasonId : undefined}
            onClick={handleOpenOrStart}
          >
            {submitting && !discardsPlan ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <FilePlus2 aria-hidden data-icon="inline-start" />
            )}
            {t("blank_script_start")}
          </Button>
          {busyReason !== null && (
            <span id={reasonId} className="sr-only">
              {busyReason}
            </span>
          )}
        </TooltipTrigger>
        <TooltipContent>{busyReason ?? t("blank_script_hint")}</TooltipContent>
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
              <AlertDialogTitle>{discardTitle}</AlertDialogTitle>
            </AlertDialogHeader>
            <AlertDialogBody tabIndex={0} role="region" aria-label={discardTitle}>
              <div className="flex flex-col gap-3">
                <AlertDialogDescription>{t("blank_script_discard_desc")}</AlertDialogDescription>
                {busyReason !== null && (
                  <p role="alert" className="text-sm text-warn">
                    {busyReason}
                  </p>
                )}
              </div>
            </AlertDialogBody>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction variant="destructive" disabled={entryDisabled} onClick={() => confirmLeave(start)}>
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
