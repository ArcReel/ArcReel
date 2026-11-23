import type { TFunction } from "i18next";

interface PromptAuthoringHandoff {
  episodeRef: string;
  scopeLabel: string;
  rewrite: boolean;
  instructions: string;
}

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
