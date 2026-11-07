import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Loader2, Plus, Scissors } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useWorkflowStore, workflowPlanKey } from "@/stores/workflow-store";
import { refusalReason } from "@/components/workflow/step-list";
import type { EditTimelineReadout } from "@/types/edit-timeline";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";
import { createScriptEditTimeline } from "./create-script-timeline";

interface EditTimelineEmptyStateProps {
  projectName: string;
  episode: number;
  /** Creation callback lets editing select the new timeline. */
  onCreated: (created: EditTimelineReadout) => void;
}

/**
 * Editing-view empty state when this episode has no timeline.
 *
 * Same two entries as WorkflowPanel editing: Agent handoff only prefills chat/opens its panel for user-confirmed sending;
 * timeline creation mechanically derives from scripts without Agents. Rendering never automatically creates timelines.
 */
export function EditTimelineEmptyState({
  projectName,
  episode,
  onCreated,
}: EditTimelineEmptyStateProps) {
  const { t } = useTranslation(["dashboard", "workflow"]);
  const ledger = useEpisodeLedger();
  const [creating, setCreating] = useState(false);
  // Admission uses the same episode production-status operation (at least one usable video), matching server creation refusal;
  // while unavailable or from another episode, do not disable; server rechecks on submission.
  const operation = useWorkflowStore((s) =>
    s.planKey === workflowPlanKey(projectName, episode) ? s.plan?.status.operations.create_edit_timeline : undefined,
  );
  const blockedReason = refusalReason(t, operation) ?? undefined;
  const blocked = blockedReason !== undefined;

  const handleHandToAgent = useCallback(() => {
    // Prefill only, never send; existing sessions neither switch nor create anew.
    const episodeRef = episodeAgentRef(ledger, episode, t);
    useAssistantStore.getState().setInput(t("workflow:agent_prefill_create_edit_timeline", { episodeRef }));
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [episode, ledger, t]);

  const handleCreate = async () => {
    if (creating || blocked) return;
    setCreating(true);
    try {
      // Newly created timeline immediately becomes the selected tab without another toast.
      onCreated((await createScriptEditTimeline(projectName, episode, t)).created);
    } catch (err) {
      useAppStore.getState().pushToast(t("edit_timeline_create_failed", { message: errMsg(err) }), "error");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-8">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <span
          aria-hidden="true"
          className="grid size-11 place-items-center rounded-xl border border-primary/25 bg-primary/10 text-primary"
        >
          <Scissors className="size-5" />
        </span>
        <div className="flex flex-col gap-1.5">
          <h2 className="text-base font-semibold text-foreground">{t("edit_view_empty_title")}</h2>
          <p className="text-sm text-muted-foreground">{t("edit_view_empty_description")}</p>
        </div>
        {/* Disabled buttons do not hover; attach admission reasons to their outer container. */}
        <div className="flex flex-wrap items-center justify-center gap-2" title={blockedReason}>
          <Button size="sm" onClick={handleHandToAgent} disabled={blocked}>
            <Bot data-icon="inline-start" aria-hidden />
            {t("workflow:act_agent_edit")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void handleCreate()} disabled={blocked || creating}>
            {creating ? (
              <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
            ) : (
              <Plus data-icon="inline-start" aria-hidden />
            )}
            {t("workflow:act_create_edit_timeline")}
          </Button>
        </div>
        {blocked && <p className="text-xs text-muted-foreground">{blockedReason}</p>}
      </div>
    </div>
  );
}
