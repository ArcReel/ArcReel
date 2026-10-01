import { useEffect, useId, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bot, Sparkles } from "lucide-react";
import { API } from "@/api";
import { enqueueScriptPlan, scriptPlanResourceId } from "@/actions/generation";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useScriptPlanStore, type ScriptPlanOpenRequest } from "@/stores/script-plan-store";
import { isResourceBusy } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";

const FIELD_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

interface HostProps {
  projectName: string;
  episode: number;
  /** 本集上次保存的附加指令。 */
  savedInstructions?: string;
}

/** 集页上唯一的「AI 规划脚本」弹窗宿主：按 {@link useScriptPlanStore} 的请求打开。 */
export function ScriptPlanHost({ projectName, episode, savedInstructions }: HostProps) {
  const request = useScriptPlanStore((s) => s.request);
  const close = useScriptPlanStore((s) => s.close);
  useEffect(() => close, [close, projectName, episode]);
  if (!request || request.projectName !== projectName || request.episode !== episode) return null;
  return (
    <ScriptPlanDialog
      // 每次打开都从请求与已保存的指令重新初始化，不沿用上一次弹窗里未提交的输入。
      key={`${projectName}:${episode}:${request.replaces}`}
      request={request}
      savedInstructions={savedInstructions ?? ""}
      onClose={close}
    />
  );
}

interface DialogProps {
  request: ScriptPlanOpenRequest;
  savedInstructions: string;
  onClose: () => void;
}

export function ScriptPlanDialog({ request, savedInstructions, onClose }: DialogProps) {
  const { t } = useTranslation("dashboard");
  const episodeLedger = useEpisodeLedger();
  const titleId = useId();
  const descId = useId();
  const fieldId = useId();
  const { projectName, episode, replaces } = request;
  const regenerate = replaces !== "none" && replaces !== "formal_script";
  // 未确认的规划与待修复草稿没有版本历史，整份替换即丢失，弹窗本身就是替换前的确认。
  const lossText =
    replaces === "pending_plan"
      ? t("script_plan_replace_pending_warning")
      : replaces === "draft"
        ? t("script_plan_replace_draft_warning")
        : null;
  const [instructions, setInstructions] = useState(savedInstructions);
  const [submitting, setSubmitting] = useState(false);

  const guardBusy = (): boolean => {
    if (isResourceBusy("text_script_plan", projectName, scriptPlanResourceId(episode))) {
      useAppStore.getState().pushToast(t("script_plan_busy"), "error");
      return true;
    }
    return false;
  };

  const submit = async () => {
    if (submitting || guardBusy()) return;
    setSubmitting(true);
    try {
      await enqueueScriptPlan(projectName, episode, { instructions: instructions.trim() || null });
      onClose();
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
    const lines = [t("script_plan_agent_prefill", { episodeRef: episodeAgentRef(episodeLedger, episode, t) })];
    if (lossText) lines.push(t("script_plan_agent_prefill_replace"));
    if (instructions.trim()) {
      lines.push(t("script_plan_agent_prefill_instructions", { instructions: instructions.trim() }));
    }
    useAssistantStore.getState().setInput(lines.join("\n"));
    useAppStore.getState().setAssistantPanelOpen(true);
    setSubmitting(false);
    onClose();
  };

  return (
    <GlassModal
      open
      onClose={() => {
        if (!submitting) onClose();
      }}
      labelledBy={titleId}
      describedBy={descId}
      widthClassName="w-full max-w-lg"
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
    >
      <div className="p-5">
        <h2
          id={titleId}
          className="display-serif text-[17px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {regenerate ? t("script_plan_regenerate_title") : t("script_plan_title")}
        </h2>
        <p id={descId} className="mt-1.5 text-[12.5px] leading-[1.55]" style={{ color: "var(--color-text-3)" }}>
          {t("script_plan_desc")}
          {(replaces === "confirmed_plan" || replaces === "formal_script") && ` ${t("script_plan_replace_confirmed_hint")}`}
        </p>

        {lossText && (
          <div
            role="alert"
            className="mt-3 flex items-start gap-2 rounded-lg border border-red-500/35 px-3 py-2 text-[12px] leading-[1.55] text-red-300"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{lossText}</span>
          </div>
        )}

        <label htmlFor={fieldId} className="mt-4 block text-[12px] font-medium" style={{ color: "var(--color-text-2)" }}>
          {t("script_plan_instructions_label")}
        </label>
        <textarea
          id={fieldId}
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          rows={3}
          maxLength={4000}
          placeholder={t("script_plan_instructions_placeholder")}
          className="focus-ring mt-1.5 w-full resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
          style={FIELD_STYLE}
        />

        <div className="mt-4 flex items-center justify-end gap-2">
          <SecondaryButton
            size="sm"
            onClick={() => void submit()}
            disabled={submitting}
            leadingIcon={<Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            {regenerate ? t("script_plan_ai_regenerate") : t("script_plan_ai_plan")}
          </SecondaryButton>
          <PrimaryButton
            size="sm"
            onClick={() => void handOff()}
            disabled={submitting}
            leadingIcon={<Bot className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            {t("script_plan_hand_to_agent")}
          </PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
