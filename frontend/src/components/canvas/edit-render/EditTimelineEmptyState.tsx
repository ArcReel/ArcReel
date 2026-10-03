import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Loader2, Plus, Scissors } from "lucide-react";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { refusalReason } from "@/components/workflow/step-list";
import type { EditTimelineReadout } from "@/types/edit-timeline";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";
import { createScriptEditTimeline } from "./create-script-timeline";

interface EditTimelineEmptyStateProps {
  projectName: string;
  episode: number;
  /** 新建成功后回调，剪辑视图据此切到新的剪辑时间线。 */
  onCreated: (created: EditTimelineReadout) => void;
}

/**
 * 剪辑视图在本集还没有剪辑时间线时的空状态。
 *
 * 两个入口与 WorkflowPanel「剪辑」行同一套：「交给 Agent 剪辑」只把请求预填进对话输入框并打开面板，
 * 由用户确认发送；「新建剪辑时间线」直接按脚本机械新建，不经过 Agent。出片从不自动新建剪辑时间线。
 */
export function EditTimelineEmptyState({
  projectName,
  episode,
  onCreated,
}: EditTimelineEmptyStateProps) {
  const { t } = useTranslation(["dashboard", "workflow"]);
  const ledger = useEpisodeLedger();
  const [creating, setCreating] = useState(false);
  // 准入取集页制作状态里的同一操作（本集至少有一个可用视频），与服务端新建时的拒绝同源；
  // 制作状态尚未取回或属于别的集时不置灰，提交时由服务端复核。
  const operation = useWorkflowStore((s) =>
    s.planKey === `${projectName}::${episode}` ? s.plan?.status.operations.create_edit_timeline : undefined,
  );
  const blockedReason = refusalReason(t, operation) ?? undefined;
  const blocked = blockedReason !== undefined;

  const handleHandToAgent = useCallback(() => {
    // 只填不发送，已有会话时不切换、不新建
    const episodeRef = episodeAgentRef(ledger, episode, t);
    useAssistantStore.getState().setInput(t("workflow:agent_prefill_create_edit_timeline", { episodeRef }));
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [episode, ledger, t]);

  const handleCreate = async () => {
    if (creating || blocked) return;
    setCreating(true);
    try {
      const created = await createScriptEditTimeline(projectName, episode, t);
      useAppStore.getState().pushToast(t("workflow:edit_timeline_created", { name: created.timeline.name }), "success");
      onCreated(created);
    } catch (err) {
      useAppStore.getState().pushToast(t("edit_timeline_create_failed", { message: errMsg(err) }), "error");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <span
          aria-hidden="true"
          className="grid h-11 w-11 place-items-center rounded-xl"
          style={{
            background: "color-mix(in oklab, var(--primary) 12%, transparent)",
            border: "1px solid color-mix(in oklab, var(--primary) 22%, transparent)",
            color: "var(--primary)",
          }}
        >
          <Scissors className="h-5 w-5" />
        </span>
        <div>
          <h2 className="display-serif text-[16px] font-semibold" style={{ color: "var(--foreground)" }}>
            {t("edit_view_empty_title")}
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-[1.6]" style={{ color: "var(--muted-foreground)" }}>
            {t("edit_view_empty_description")}
          </p>
        </div>
        {/* 禁用的 button 不触发悬停，准入原因挂在外层容器上。 */}
        <div className="flex flex-wrap items-center justify-center gap-2" title={blockedReason}>
          <PrimaryButton
            tone="accent"
            size="sm"
            onClick={handleHandToAgent}
            disabled={blocked}
            leadingIcon={<Bot className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            {t("workflow:act_agent_edit")}
          </PrimaryButton>
          <SecondaryButton
            size="sm"
            onClick={() => void handleCreate()}
            disabled={blocked || creating}
            leadingIcon={
              creating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              )
            }
          >
            {t("workflow:act_create_edit_timeline")}
          </SecondaryButton>
        </div>
        {blocked && (
          <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
            {blockedReason}
          </p>
        )}
      </div>
    </div>
  );
}
