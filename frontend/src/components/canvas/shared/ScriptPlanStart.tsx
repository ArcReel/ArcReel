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
  /** 本集上次保存的附加指令。 */
  savedInstructions: string;
  /**
   * 提交前先落定同页的相关修改（如本集原文），返回是否继续；缺省时直接提交。
   * 规划读的是已保存的内容，修改没落定就提交会按旧内容规划。
   */
  prepare?: () => Promise<boolean>;
}

/**
 * 首次规划脚本的唯一入口：说明、附加指令、「交给 Agent」「AI 规划」，以及不用 AI 的「从空白开始」。
 * 规划在跑时换成状态说明。制作进度点名「脚本规划」时把焦点交给附加指令。
 */
export function ScriptPlanStart(props: ScriptPlanStartProps) {
  // 换集时整块重建：集页直接切到另一集不会重新挂载，附加指令草稿会被当成下一集的指令提交并保存
  return <ScriptPlanStartForm key={`${props.projectName}:${props.episode}`} {...props} />;
}

function ScriptPlanStartForm({ projectName, episode, savedInstructions, prepare }: ScriptPlanStartProps) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const fieldId = useId();
  const reasonId = useId();
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  // 没动过输入框时跟随最新保存的指令（另一标签页或 Agent 保存的），动过之后保留自己的输入
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
