import type { TFunction } from "i18next";

interface PromptAuthoringHandoff {
  /** 集名连同集 ID 的指称（`episodeAgentRef`）。 */
  episodeRef: string;
  /** 已成文的范围：「全部待编写的 N 条」或逐条 ID。 */
  scopeLabel: string;
  rewrite: boolean;
  instructions: string;
}

/** 提示词编写「交给 Agent」时预填进输入框的文本；弹窗与制作进度面板共用。 */
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
