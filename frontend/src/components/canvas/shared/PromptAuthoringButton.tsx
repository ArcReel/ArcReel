import { useTranslation } from "react-i18next";
import { PenLine } from "lucide-react";
import { promptAuthoringResourceId } from "@/actions/generation";
import { Button } from "@/components/ui/button";
import { usePromptAuthoringStore, type PromptAuthoringScope } from "@/stores/prompt-authoring-store";
import { TextTaskProgress } from "@/components/shared/TextTaskProgress";
import { selectActiveTasks, useTasksStore, useActiveResourceIds } from "@/stores/tasks-store";

interface Props {
  projectName: string;
  episode: number;
  scope: PromptAuthoringScope;
  currentEntryId?: string | null;
  variant?: "outline" | "ghost";
}

export function PromptAuthoringButton({ projectName, episode, scope, currentEntryId, variant = "outline" }: Props) {
  const { t } = useTranslation("dashboard");
  const open = usePromptAuthoringStore((s) => s.open);
  const resourceId = promptAuthoringResourceId(episode);
  const task = useTasksStore((s) => selectActiveTasks(s).find((row) => row.project_name === projectName &&
    row.task_type === "text_episode_script" && row.resource_id === resourceId));
  const busy = useActiveResourceIds("text_episode_script", projectName).has(resourceId);
  return (
    <>
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
      {task && <TextTaskProgress task={task} />}
    </>
  );
}
