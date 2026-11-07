import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { enqueueScriptPlan, scriptPlanResourceId } from "@/actions/generation";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";

export interface ScriptPlanSubmit {
  /** Submission or handoff request is in flight. */
  submitting: boolean;
  /** Direct AI planning: notify without submitting if this episode already has planning in progress. Return whether submitted. */
  submit: (instructions: string) => Promise<boolean>;
  /** Agent handoff: save per-episode additional instructions first, then prefill the planning request in the Agent input and open the panel. Return whether handed off. */
  handOff: (instructions: string) => Promise<boolean>;
}

/**
 * Two submission methods for AI script planning, shared by initial planning and Replan script dialogs.
 * `replacing` means the new plan wholly replaces an unconfirmed plan or repairable draft; explicitly warn of lost edits in Agent handoff text.
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
