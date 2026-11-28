import { useEffect, useId, useRef, useState } from "react";
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
type BlankScriptBlockingKind = "text_script_plan" | "text_episode_plan" | "text_episode_script";

function busyReasonFor(
  episode: number,
  isBusy: (kind: BlankScriptBlockingKind, resourceId: string) => boolean,
): BusyReasonKey | null {
  if (isBusy("text_script_plan", scriptPlanResourceId(episode))) return "script_plan_busy";
  if (EPISODE_PLANNING_SLOTS.some((slot) => isBusy("text_episode_plan", slot))) {
    return "episode_planning_busy";
  }
  if (isBusy("text_episode_script", promptAuthoringResourceId(episode))) return "ad_script_busy";
  return null;
}

function latestBusyReasonKey(projectName: string, episode: number): BusyReasonKey | null {
  return busyReasonFor(episode, (kind, resourceId) => isResourceBusy(kind, projectName, resourceId));
}

interface Props {
  projectName: string;
  episode: number;
  discardsPlan: boolean;
  disabled?: boolean;
  variant?: "outline" | "ghost";
  className?: string;
  onSubmittingChange?: (submitting: boolean) => void;
}

export function StartBlankScriptButton(props: Props) {
  return <StartBlankScriptButtonInner key={`${props.projectName}:${props.episode}`} {...props} />;
}

function StartBlankScriptButtonInner({
  projectName,
  episode,
  discardsPlan,
  disabled = false,
  variant = "outline",
  className,
  onSubmittingChange,
}: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const confirmLeave = useConfirmLeave();
  const reasonId = useId();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);
  const onSubmittingChangeRef = useRef(onSubmittingChange);

  useEffect(() => {
    onSubmittingChangeRef.current = onSubmittingChange;
  }, [onSubmittingChange]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (submittingRef.current) {
        submittingRef.current = false;
        onSubmittingChangeRef.current?.(false);
      }
    };
  }, []);

  const activeByKind: Record<BlankScriptBlockingKind, ReadonlySet<string>> = {
    text_episode_plan: useActiveResourceIds("text_episode_plan", projectName),
    text_script_plan: useActiveResourceIds("text_script_plan", projectName),
    text_episode_script: useActiveResourceIds("text_episode_script", projectName),
  };
  const busyReasonKey = busyReasonFor(episode, (kind, resourceId) => activeByKind[kind].has(resourceId));
  const busyReason = busyReasonKey ? t(busyReasonKey) : null;
  const entryDisabled = submitting || disabled || busyReason !== null;

  const start = async (): Promise<boolean> => {
    if (submittingRef.current || disabled) return false;
    const latestBusy = latestBusyReasonKey(projectName, episode);
    if (latestBusy !== null) {
      useAppStore.getState().pushToast(t(latestBusy), "error");
      return false;
    }
    submittingRef.current = true;
    setSubmitting(true);
    onSubmittingChangeRef.current?.(true);
    try {
      await API.startBlankScript(projectName, episode);
      if (mountedRef.current) setConfirmOpen(false);
      await refreshAfterWrite(projectName, t, { scope: { episodes: [episode] } });
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(t("blank_script_failed", { message: errMsg(err) }), "error");
      return false;
    } finally {
      if (mountedRef.current && submittingRef.current) {
        submittingRef.current = false;
        setSubmitting(false);
        onSubmittingChangeRef.current?.(false);
      }
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
  const [blankSubmitting, setBlankSubmitting] = useState(false);
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 text-muted-foreground", className)}>
      <p>{t("timeline_no_script_blank_hint")}</p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <AdScriptButton
          projectName={projectName}
          episode={episode}
          regenerate={false}
          prominent
          disabled={blankSubmitting}
        />
        <StartBlankScriptButton
          projectName={projectName}
          episode={episode}
          discardsPlan={false}
          onSubmittingChange={setBlankSubmitting}
        />
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
