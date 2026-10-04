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

/**
 * 首次规划脚本的唯一入口：说明、附加指令、「交给 Agent」「AI 规划」，以及不用 AI 的「从空白开始」。
 * 规划在跑时换成状态说明。制作进度点名「脚本规划」时把焦点交给附加指令。
 */
export function ScriptPlanStart({
  projectName,
  episode,
  savedInstructions,
}: {
  projectName: string;
  episode: number;
  /** 本集上次保存的附加指令。 */
  savedInstructions: string;
}) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const fieldId = useId();
  const reasonId = useId();
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const [instructions, setInstructions] = useState(savedInstructions);
  const { busy, latestTask, refusedReason } = useScriptPlanEntry(projectName, episode);
  const { submitting, submit, handOff } = useScriptPlanSubmit(projectName, episode);

  useEpisodeSurfaceRequest(projectName, episode, "script_plan", () => {
    fieldRef.current?.focus();
    fieldRef.current?.scrollIntoView({ block: "center" });
  });

  const disabled = submitting || refusedReason !== null;
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
              onChange={(event) => setInstructions(event.target.value)}
              maxLength={4000}
              placeholder={t("script_plan_instructions_placeholder")}
              disabled={submitting}
              className="max-h-40"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={disabled}
              aria-describedby={refusedReason ? reasonId : undefined}
              onClick={() => void handOff(instructions)}
            >
              <Bot aria-hidden data-icon="inline-start" />
              {t("script_plan_hand_to_agent")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={disabled}
              aria-describedby={refusedReason ? reasonId : undefined}
              onClick={() => void submit(instructions)}
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
