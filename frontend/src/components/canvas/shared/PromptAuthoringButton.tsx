import { useTranslation } from "react-i18next";
import { PenLine } from "lucide-react";
import { promptAuthoringResourceId } from "@/actions/generation";
import { usePromptAuthoringStore, type PromptAuthoringScope } from "@/stores/prompt-authoring-store";
import { useActiveResourceIds } from "@/stores/tasks-store";

interface Props {
  projectName: string;
  episode: number;
  scope: PromptAuthoringScope;
  currentEntryId?: string | null;
  className?: string;
}

/** 打开「编写提示词」弹窗的入口；本集的提示词编写在跑时禁用。 */
export function PromptAuthoringButton({ projectName, episode, scope, currentEntryId, className = "" }: Props) {
  const { t } = useTranslation("dashboard");
  const open = usePromptAuthoringStore((s) => s.open);
  const busy = useActiveResourceIds("text_episode_script", projectName).has(promptAuthoringResourceId(episode));
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-50 ${className}`.trim()}
      disabled={busy}
      title={busy ? t("prompt_authoring_busy") : t("prompt_authoring_open")}
      onClick={() => open({ projectName, episode, scope, currentEntryId })}
    >
      <PenLine className="h-3 w-3" aria-hidden="true" />
      <span>{t("prompt_authoring_open")}</span>
    </button>
  );
}
