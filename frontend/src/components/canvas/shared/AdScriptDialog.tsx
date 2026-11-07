import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { cn } from "cn";
import { Bot, CheckCircle2, Loader2, RotateCcw, Sparkles, X } from "lucide-react";
import { ApiRequestError } from "@/api";
import { enqueueAdScript, promptAuthoringResourceId } from "@/actions/generation";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isAdScriptTask, useAdScriptEntry } from "@/hooks/useAdScriptEntry";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAdScriptStore, type AdScriptOpenRequest } from "@/stores/ad-script-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { AdScriptTaskResult, ScriptOverwrite } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";
import { formatNameList } from "@/utils/list-format";

function readScriptOverwrite(err: unknown): ScriptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic as { script_overwrite?: ScriptOverwrite } | undefined;
  return diagnostic?.script_overwrite ?? null;
}

/** Sole episode-page ad/short-film AI-script dialog host, opening for {@link useAdScriptStore} requests. */
export function AdScriptHost({ projectName, episode }: { projectName: string; episode: number }) {
  const request = useAdScriptStore((s) => s.request);
  const close = useAdScriptStore((s) => s.close);
  useEffect(() => close, [close, projectName, episode]);
  if (!request || request.projectName !== projectName || request.episode !== episode) return null;
  return (
    <AdScriptDialog
      // Reinitialize every opening, not reusing previously unsubmitted inputs.
      key={`${projectName}:${episode}:${request.regenerate}`}
      request={request}
      onClose={close}
    />
  );
}

interface DialogProps {
  request: AdScriptOpenRequest;
  onClose: () => void;
}

/**
 * Whole ad/short-film generation: AI generation submits directly to formal scripts; Agent handoff prefills the same request.
 * Whole redo first receives server rejection/loss lists, then resubmits with list versions after confirmation. Extra instructions apply only to this request, unsaved.
 */
export function AdScriptDialog({ request, onClose }: DialogProps) {
  const { t } = useTranslation(["dashboard", "workflow"]);
  const episodeLedger = useEpisodeLedger();
  const fieldId = useId();
  const { projectName, episode, regenerate } = request;
  const [instructions, setInstructions] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [overwrite, setOverwrite] = useState<ScriptOverwrite | null>(null);

  const submit = async (overwriteRevision: string | null) => {
    if (submitting) return;
    if (isResourceBusy("text_episode_script", projectName, promptAuthoringResourceId(episode))) {
      useAppStore.getState().pushToast(t("ad_script_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueAdScript(projectName, episode, {
        instructions: instructions.trim() || null,
        regenerate,
        overwrite_revision: overwriteRevision,
      });
      setOverwrite(null);
      onClose();
    } catch (err) {
      const loss = readScriptOverwrite(err);
      setOverwrite(loss);
      if (!loss) useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handOff = () => {
    const episodeRef = episodeAgentRef(episodeLedger, episode, t);
    const lines = [
      regenerate
        ? t("ad_script_agent_prefill_regenerate", { episodeRef })
        : t("workflow:agent_prefill_generate_script", { episodeRef }),
    ];
    const extra = instructions.trim();
    if (extra) lines.push(t("workflow:agent_prefill_instructions", { instructions: extra }));
    useAssistantStore.getState().setInput(lines.join("\n"));
    useAppStore.getState().setAssistantPanelOpen(true);
    onClose();
  };

  const actionLabel = regenerate ? t("ad_script_regenerate") : t("ad_script_generate");

  return (
    <>
      <Dialog
        open={overwrite === null}
        onOpenChange={(next) => {
          if (!next && !submitting) onClose();
        }}
      >
        <DialogContent showCloseButton={!submitting}>
          <DialogHeader>
            <DialogTitle>{actionLabel}</DialogTitle>
            <DialogDescription>{regenerate ? t("ad_script_regenerate_desc") : t("ad_script_desc")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={fieldId}>{t("ad_script_instructions_label")}</Label>
              <Textarea
                id={fieldId}
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                maxLength={4000}
                placeholder={t("ad_script_instructions_placeholder")}
                disabled={submitting}
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => void submit(null)} disabled={submitting}>
              <Sparkles aria-hidden data-icon="inline-start" />
              {actionLabel}
            </Button>
            <Button onClick={handOff} disabled={submitting}>
              <Bot aria-hidden data-icon="inline-start" />
              {t("script_plan_hand_to_agent")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {overwrite && (
        <ScriptOverwriteConfirmDialog
          open
          overwrite={overwrite}
          loading={submitting}
          title={t("ad_script_overwrite_title")}
          confirmLabel={t("ad_script_overwrite_confirm")}
          loadingLabel={t("ad_script_overwrite_confirm")}
          onConfirm={() => void submit(overwrite.revision)}
          onCancel={() => setOverwrite(null)}
        />
      )}
    </>
  );
}

interface ButtonProps {
  projectName: string;
  episode: number;
  regenerate: boolean;
  /** Header actions use small outlined buttons; empty-state primary actions use solid buttons. */
  prominent?: boolean;
}

/**
 * Ad/short-film AI-script dialog entry; `regenerate` labels script regeneration.
 * Remain visible but disabled while running or missing inspiration/products, explaining reasons on hover.
 */
export function AdScriptButton({ projectName, episode, regenerate, prominent = false }: ButtonProps) {
  const { t } = useTranslation("dashboard");
  const open = useAdScriptStore((s) => s.open);
  const { busy, refusedReason } = useAdScriptEntry(projectName, episode);
  const reasonId = useId();
  const reason = busy ? t("ad_script_busy") : refusedReason;
  const Icon = busy ? Loader2 : regenerate ? RotateCcw : Sparkles;
  const button = (
    <Button
      variant={prominent ? "default" : "outline"}
      size={prominent ? "default" : "sm"}
      disabled={reason !== null}
      aria-describedby={reason !== null ? reasonId : undefined}
      onClick={() => open({ projectName, episode, regenerate })}
    >
      <Icon className={busy ? "animate-spin" : undefined} aria-hidden data-icon="inline-start" />
      {regenerate ? t("ad_script_regenerate") : t("ad_script_generate")}
    </Button>
  );
  if (reason === null) return button;
  // Disabled buttons do not hover; outer spans carry hints, with reasons additionally in sr-only button descriptions.
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex" />}>
        {button}
        <span id={reasonId} className="sr-only">
          {reason}
        </span>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  );
}

interface ProgressProps {
  projectName: string;
  episode: number;
  /**
   * Without formal scripts, episode script-text tasks can only be whole generation; show queued/running states.
   * With formal scripts, show only whole-generation tasks (sharing prompt-authoring occupancy slots), then list newly registered assets.
   */
  noScript: boolean;
  className?: string;
}

const STATUS_CLS = "flex gap-2.5 rounded-xl border border-primary/20 bg-primary/10 px-4 py-3 text-sm text-subtle-foreground";

/**
 * Ad/short-film whole-generation progress: queued/running, or newly registered pending assets.
 * Episode-page `TextTaskFailureNote` already presents failure reasons/recovery; do not duplicate here.
 */
export function AdScriptProgress({ projectName, episode, noScript, className = "" }: ProgressProps) {
  const { t, i18n } = useTranslation("dashboard");
  const { busy, latestTask } = useAdScriptEntry(projectName, episode);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const relevant = noScript || isAdScriptTask(latestTask);
  if (!latestTask || !relevant || dismissed === latestTask.task_id) return null;

  if (busy) {
    return (
      <div
        role="status"
        className={cn(STATUS_CLS, "items-center", className)}
      >
        <Loader2 className="size-4 shrink-0 animate-spin text-primary" aria-hidden />
        <span>
          {latestTask.status === "running" ? t("ad_script_progress_running") : t("ad_script_progress_queued")}{" "}
          <span className="text-muted-foreground">{t("ad_script_progress_hint")}</span>
        </span>
      </div>
    );
  }

  const dismiss = (
    <Button
      variant="ghost"
      size="icon-xs"
      onClick={() => setDismissed(latestTask.task_id)}
      aria-label={t("ad_script_dismiss")}
      className="ml-auto"
    >
      <X aria-hidden="true" />
    </Button>
  );

  const registered = (latestTask.result as AdScriptTaskResult | null)?.new_assets ?? [];
  if (latestTask.status !== "succeeded" || registered.length === 0) return null;
  return (
    <div
      role="status"
      className={cn(STATUS_CLS, "items-start", className)}
    >
      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
      <span>
        {t("ad_script_new_assets", {
          count: registered.length,
          names: formatNameList(
            registered.map((asset) => asset.name),
            i18n.language,
          ),
        })}
      </span>
      {dismiss}
    </div>
  );
}

/** Disabled-entry destinations without formal scripts: inspiration in project overview, products on product pages, both entered from overview. */
export function AdScriptInputsLink({ className = "" }: { className?: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <Link href="/" className={cn("rounded-sm underline underline-offset-2 focus-ring", className)}>
      {t("ad_script_edit_inputs")}
    </Link>
  );
}
