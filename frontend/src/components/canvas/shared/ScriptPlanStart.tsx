import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Sparkles } from "lucide-react";

import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useScriptPlanEntry } from "@/hooks/useScriptPlanEntry";
import { useScriptPlanSubmit } from "@/hooks/useScriptPlanSubmit";
import { useEpisodeSurfaceRequest } from "@/stores/episode-surface-store";

interface ScriptPlanStartProps {
  projectName: string;
  episode: number;
  /** Last saved extra instructions for this episode. */
  savedInstructions: string;
  /**
   * Settle related edits on this page (e.g. episode source) before submission; return whether to continue. Omission submits directly.
   * Planning reads saved content; unsettled edits would plan against old content.
   */
  prepare?: () => Promise<boolean>;
}

/**
 * Sole first-script-plan entry: explanation, extra instructions, Agent handoff/AI planning, and non-AI start-from-blank.
 * Running plans show status instead. Focus extra instructions when production progress targets script planning.
 */
export function ScriptPlanStart(props: ScriptPlanStartProps) {
  // Rebuild the whole block on episode changes: direct navigation does not remount and could submit/save drafts as the next episode's instructions.
  return <ScriptPlanStartForm key={`${props.projectName}:${props.episode}`} {...props} />;
}

function ScriptPlanStartForm({ projectName, episode, savedInstructions, prepare }: ScriptPlanStartProps) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const fieldId = useId();
  const reasonId = useId();
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  // Untouched inputs follow latest saved instructions from other tabs/Agents; once edited, preserve local input.
  const [draft, setDraft] = useState<string | null>(null);
  const instructions = draft ?? savedInstructions;
  const { busy, latestTask, refusedReason } = useScriptPlanEntry(projectName, episode);
  const { submitting, submit, handOff } = useScriptPlanSubmit(projectName, episode);
  const [preparing, setPreparing] = useState(false);

  useEpisodeSurfaceRequest(projectName, episode, "script_plan", () => {
    fieldRef.current?.focus();
    fieldRef.current?.scrollIntoView({ block: "center" });
  });

  const run = async (action: (instructions: string) => Promise<boolean>) => {
    if (prepare) {
      setPreparing(true);
      try {
        if (!(await prepare())) return;
      } finally {
        setPreparing(false);
      }
    }
    await action(instructions);
  };

  const disabled = submitting || preparing || refusedReason !== null;
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
      <div className="flex flex-col gap-1">
        <h2 id={titleId} className="text-base font-semibold text-foreground">
          {t("script_plan_start_title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("script_plan_desc")}</p>
      </div>
      {busy ? (
        <p role="status" className="flex items-center gap-2 rounded-lg bg-primary/10 px-3 py-2 text-sm text-foreground">
          <span aria-hidden className="size-1.5 shrink-0 animate-breath rounded-full bg-primary" />
          <span>
            {latestTask?.status === "running" ? t("script_plan_progress_running") : t("script_plan_progress_queued")}{" "}
            <span className="text-subtle-foreground">{t("script_plan_progress_hint")}</span>
          </span>
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={fieldId}>{t("script_plan_instructions_label")}</Label>
            <Textarea
              ref={fieldRef}
              id={fieldId}
              value={instructions}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={4000}
              placeholder={t("script_plan_instructions_placeholder")}
              disabled={submitting || preparing}
              className="max-h-40"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={disabled}
              aria-describedby={refusedReason ? reasonId : undefined}
              onClick={() => void run(handOff)}
            >
              <Bot aria-hidden data-icon="inline-start" />
              {t("script_plan_hand_to_agent")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={disabled}
              aria-describedby={refusedReason ? reasonId : undefined}
              onClick={() => void run(submit)}
            >
              <Sparkles aria-hidden data-icon="inline-start" />
              {t("script_plan_ai_plan")}
            </Button>
            <StartBlankScriptButton
              projectName={projectName}
              episode={episode}
              discardsPlan={false}
              variant="ghost"
              className="ml-auto"
            />
          </div>
          {refusedReason && (
            <p id={reasonId} className="text-sm text-warn">
              {refusedReason}
            </p>
          )}
        </>
      )}
    </section>
  );
}
