import { useTranslation } from "react-i18next";
import { PenLine } from "lucide-react";
import { promptAuthoringResourceId } from "@/actions/generation";
import { Button } from "@/components/ui/button";
import { usePromptAuthoringStore, type PromptAuthoringScope } from "@/stores/prompt-authoring-store";
import { useActiveResourceIds } from "@/stores/tasks-store";

interface Props {
  projectName: string;
  episode: number;
  scope: PromptAuthoringScope;
  currentEntryId?: string | null;
  variant?: "outline" | "ghost";
}

/** Open the prompt-authoring dialog; disabled while prompt authoring is running for this episode. */
export function PromptAuthoringButton({ projectName, episode, scope, currentEntryId, variant = "outline" }: Props) {
  const { t } = useTranslation("dashboard");
  const open = usePromptAuthoringStore((s) => s.open);
  const busy = useActiveResourceIds("text_episode_script", projectName).has(promptAuthoringResourceId(episode));
  return (
    <Button
      variant={variant}
      size="sm"
      disabled={busy}
      title={busy ? t("prompt_authoring_busy") : undefined}
      onClick={() => open({ projectName, episode, scope, currentEntryId })}
    >
      <PenLine aria-hidden data-icon="inline-start" />
      {t("prompt_authoring_open")}
    </Button>
  );
}
