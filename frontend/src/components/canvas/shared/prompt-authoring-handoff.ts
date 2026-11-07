import type { TFunction } from "i18next";

interface PromptAuthoringHandoff {
  /** Episode name together with its ID (`episodeAgentRef`). */
  episodeRef: string;
  /** Written scope: all N pending-authoring entries or individual IDs. */
  scopeLabel: string;
  rewrite: boolean;
  instructions: string;
}

/** Prefilled input text for handing prompt authoring to the Agent, shared by the dialog and production-progress panel. */
export function promptAuthoringHandoffText(t: TFunction, handoff: PromptAuthoringHandoff): string {
  const lines = [
    t("dashboard:prompt_authoring_agent_prefill", { episodeRef: handoff.episodeRef, scope: handoff.scopeLabel }),
    handoff.rewrite
      ? t("dashboard:prompt_authoring_agent_prefill_rewrite")
      : t("dashboard:prompt_authoring_agent_prefill_fill"),
  ];
  const instructions = handoff.instructions.trim();
  if (instructions) {
    lines.push(t("dashboard:prompt_authoring_agent_prefill_instructions", { instructions }));
  }
  return lines.join("\n");
}
