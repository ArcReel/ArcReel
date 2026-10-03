// PROTOTYPE — 原文与分集视图原型（#2982）：剧集还没有脚本规划与脚本时的集页部件，不合并。
// 合并页头里的集头、内嵌的「AI 规划脚本」起步区、本集导览（顶部折叠或右侧栏）。

import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Anchor, Bot, ChevronDown, Sparkles } from "lucide-react";

import { API } from "@/api";
import { enqueueScriptPlan, scriptPlanResourceId } from "@/actions/generation";
import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useScriptPlanEntry } from "@/hooks/useScriptPlanEntry";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { EpisodeMeta } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";

/**
 * 起步区：附加要求 + 「交给 Agent」「AI 规划脚本」，下方一行「从空白开始」。
 * 与 ScriptPlanDialog 同一套提交逻辑，只是不再弹窗。
 */
export function InlineScriptPlanStart({
  projectName,
  episode,
  savedInstructions,
  compact = false,
}: {
  projectName: string;
  episode: number;
  savedInstructions: string;
  /** 放在窄栏里时收紧间距。 */
  compact?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const ledger = useEpisodeLedger();
  const { busy } = useScriptPlanEntry(projectName, episode);
  const [instructions, setInstructions] = useState(savedInstructions);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (submitting) return;
    if (isResourceBusy("text_script_plan", projectName, scriptPlanResourceId(episode))) {
      useAppStore.getState().pushToast(t("script_plan_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueScriptPlan(projectName, episode, { instructions: instructions.trim() || null });
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };
  const handOff = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await API.saveScriptPlanInstructions(projectName, episode, instructions.trim());
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      setSubmitting(false);
      return;
    }
    const lines = [t("script_plan_agent_prefill", { episodeRef: episodeAgentRef(ledger, episode, t) })];
    if (instructions.trim()) lines.push(t("script_plan_agent_prefill_instructions", { instructions: instructions.trim() }));
    useAssistantStore.getState().setInput(lines.join("\n"));
    useAppStore.getState().setAssistantPanelOpen(true);
    setSubmitting(false);
  };

  return (
    <section
      aria-labelledby="inline-plan-title"
      className={`rounded-xl border border-border bg-card ${compact ? "p-3.5" : "p-4"}`}
    >
      <h2 id="inline-plan-title" className="text-[14px] font-semibold text-foreground">
        这一集还没有脚本
      </h2>
      <p className="mt-1 text-[12.5px] leading-[1.6] text-muted-foreground">{t("script_plan_desc")}</p>
      {busy ? (
        <p role="status" className="mt-3 rounded-lg bg-primary/10 px-3 py-2 text-[12.5px] text-foreground">
          AI 正在规划脚本，完成后这里会换成规划内容，可以先去做别的事。
        </p>
      ) : (
        <>
          <label htmlFor="inline-plan-instructions" className="mt-3 block text-[12px] font-medium text-foreground/90">
            {t("script_plan_instructions_label")}
          </label>
          <Textarea
            id="inline-plan-instructions"
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            maxLength={4000}
            placeholder={t("script_plan_instructions_placeholder")}
            className="mt-1.5 max-h-40 min-h-16 text-[13px] [field-sizing:content]"
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => void handOff()} disabled={submitting}>
              <Bot />
              {t("script_plan_hand_to_agent")}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void submit()} disabled={submitting}>
              <Sparkles />
              {t("script_plan_ai_plan")}
            </Button>
            <span className="flex-1" />
            <span className="text-[12px] text-muted-foreground">不用 AI？</span>
            <StartBlankScriptButton
              projectName={projectName}
              episode={episode}
              discardsPlan={false}
              className="focus-ring rounded-md px-1.5 py-0.5 text-[12px] text-primary hover:underline"
            />
          </div>
        </>
      )}
    </section>
  );
}

/** 本集导览：节拍是顺序，保留编号；尾钩子单独一块。`side` 时纵向排在窄栏里。 */
export function GuideBlock({ meta, side }: { meta: EpisodeMeta | undefined; side: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const beats = meta?.outline?.story_beats ?? [];
  const hook = meta?.hook;
  if (beats.length === 0 && !hook) return null;

  const body = (
    <div className={side ? "space-y-3" : "space-y-2.5 px-4 pb-4 pt-3"}>
      {beats.length > 0 ? (
        <ol className={side ? "space-y-2.5" : "grid gap-2.5"} style={side ? undefined : { gridTemplateColumns: `repeat(${Math.min(beats.length, 4)}, 1fr)` }}>
          {beats.map((beat, index) => (
            <li key={index} className={`flex gap-2.5 ${side ? "" : "rounded-lg bg-muted/50 px-3 py-2.5"}`}>
              <span className="w-4 shrink-0 text-right text-[12.5px] font-semibold tabular-nums text-primary">{index + 1}</span>
              <p className="text-[12.5px] leading-[1.65] text-foreground/85">{beat}</p>
            </li>
          ))}
        </ol>
      ) : null}
      {hook ? (
        <div className="flex items-start gap-2.5 rounded-lg bg-primary/10 px-3 py-2.5">
          <Anchor className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
          <p className="text-[12.5px] leading-[1.65] text-foreground/85">
            <span className="mr-1.5 font-semibold text-primary">尾钩子</span>
            {hook}
          </p>
        </div>
      ) : null}
    </div>
  );

  if (side) {
    return (
      <section aria-labelledby="guide-side-title" className="space-y-3">
        <h3 id="guide-side-title" className="text-[12.5px] font-medium text-muted-foreground">
          本集导览
        </h3>
        {body}
      </section>
    );
  }
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card/60">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        className="focus-ring flex w-full items-center gap-2 px-4 py-2.5 text-left text-[12.5px] font-medium text-muted-foreground hover:bg-muted/40"
      >
        <ChevronDown className={`size-3.5 shrink-0 ${collapsed ? "-rotate-90" : ""}`} aria-hidden />
        本集导览
        <span className="font-normal">
          {beats.length > 0 ? `${beats.length} 个节拍` : ""}
          {beats.length > 0 && hook ? "，" : ""}
          {hook ? "尾钩子" : ""}
        </span>
      </button>
      {collapsed ? null : body}
    </section>
  );
}

/** 原文阅读列：限行宽 40em，正文用编辑体。 */
export function SourceReading({ children, toolbar }: { children: ReactNode; toolbar?: ReactNode }) {
  return (
    <article className="mx-auto w-full max-w-[40em]">
      {toolbar ? <div className="mb-3 flex items-center justify-end gap-2">{toolbar}</div> : null}
      <div className="whitespace-pre-wrap text-[14.5px] leading-[1.95] text-foreground/85 [font-family:var(--font-editorial)]">{children}</div>
    </article>
  );
}
