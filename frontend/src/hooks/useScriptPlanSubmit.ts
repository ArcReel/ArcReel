import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { enqueueScriptPlan, scriptPlanResourceId } from "@/actions/generation";
import { prefillAssistant } from "@/components/shared/DraftStatus";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";

export interface ScriptPlanSubmit {
  /** 提交或交接的请求在途。 */
  submitting: boolean;
  /** 直接 AI 规划：本集已有进行中的规划时提示并不提交。返回是否已提交。 */
  submit: (instructions: string) => Promise<boolean>;
  /** 交给 Agent：先按集保存附加指令，再把规划请求预填进 Agent 输入框并打开面板。返回是否已交接。 */
  handOff: (instructions: string) => Promise<boolean>;
}

/**
 * 「AI 规划脚本」的两种提交方式，起步区与「重新规划脚本」对话框共用这一份实现。
 * `replacing` 表示新规划会整份替换未确认的规划或待修复草稿：交给 Agent 时在预填里写明修改会丢失。
 */
export function useScriptPlanSubmit(projectName: string, episode: number, replacing = false): ScriptPlanSubmit {
  const { t } = useTranslation("dashboard");
  const ledger = useEpisodeLedger();
  const [submitting, setSubmitting] = useState(false);

  const submit = useCallback(
    async (instructions: string) => {
      if (submitting) return false;
      if (isResourceBusy("text_script_plan", projectName, scriptPlanResourceId(episode))) {
        useAppStore.getState().pushToast(t("script_plan_busy"), "error");
        return false;
      }
      setSubmitting(true);
      try {
        await enqueueScriptPlan(projectName, episode, { instructions: instructions.trim() || null });
        return true;
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [submitting, projectName, episode, t],
  );

  const handOff = useCallback(
    async (instructions: string) => {
      if (submitting) return false;
      const extra = instructions.trim();
      setSubmitting(true);
      try {
        await API.saveScriptPlanInstructions(projectName, episode, extra);
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
        return false;
      } finally {
        setSubmitting(false);
      }
      const lines = [t("script_plan_agent_prefill", { episodeRef: episodeAgentRef(ledger, episode, t) })];
      if (replacing) lines.push(t("script_plan_agent_prefill_replace"));
      if (extra) lines.push(t("script_plan_agent_prefill_instructions", { instructions: extra }));
      prefillAssistant(lines.join("\n"));
      return true;
    },
    [submitting, projectName, episode, replacing, ledger, t],
  );

  return { submitting, submit, handOff };
}
