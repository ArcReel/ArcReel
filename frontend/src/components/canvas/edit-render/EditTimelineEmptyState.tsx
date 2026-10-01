import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Loader2, Plus, Scissors } from "lucide-react";
import { API } from "@/api";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { errMsg } from "@/utils/async";
import type { CreatedEditTimeline } from "@/types";

interface EditTimelineEmptyStateProps {
  projectName: string;
  episode: number;
  /** 本集至少有一个可用视频（见 scriptHasUsableVideo）；否则两个按钮都不可点。 */
  hasUsableVideo: boolean;
  /** 新建成功后回调，剪辑视图据此切到新的剪辑时间线。 */
  onCreated: (created: CreatedEditTimeline) => void;
}

/**
 * 剪辑视图在本集还没有剪辑时间线时的空状态。
 *
 * 「交给 Agent 剪辑」只把请求预填进对话输入框并打开面板，由用户确认发送；
 * 「新建剪辑时间线」直接按脚本机械新建，不经过 Agent。出片从不自动新建剪辑时间线。
 */
export function EditTimelineEmptyState({
  projectName,
  episode,
  hasUsableVideo,
  onCreated,
}: EditTimelineEmptyStateProps) {
  const { t } = useTranslation("dashboard");
  const [creating, setCreating] = useState(false);
  const blockedReason = hasUsableVideo ? undefined : t("edit_needs_video");

  const handleHandToAgent = useCallback(() => {
    // 只填不发送，已有会话时不切换、不新建
    useAssistantStore.getState().setInput(t("edit_hand_to_agent_prefill", { episode }));
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [episode, t]);

  const handleCreate = async () => {
    if (creating || !hasUsableVideo) return;
    setCreating(true);
    try {
      const created = await API.createEditTimelineFromScript(projectName, episode, t("edit_timeline_default_name"));
      useAppStore.getState().pushToast(t("edit_timeline_created_toast", { name: created.timeline.name }), "success");
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
            background: "var(--color-accent-dim)",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
          }}
        >
          <Scissors className="h-5 w-5" />
        </span>
        <div>
          <h2 className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>
            {t("edit_empty_title")}
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-[1.6]" style={{ color: "var(--color-text-3)" }}>
            {t("edit_empty_description")}
          </p>
        </div>
        {/* 禁用的 button 不触发悬停，准入原因挂在外层容器上。 */}
        <div className="flex flex-wrap items-center justify-center gap-2" title={blockedReason}>
          <PrimaryButton
            tone="accent"
            size="sm"
            onClick={handleHandToAgent}
            disabled={!hasUsableVideo}
            leadingIcon={<Bot className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            {t("edit_hand_to_agent")}
          </PrimaryButton>
          <SecondaryButton
            size="sm"
            onClick={() => void handleCreate()}
            disabled={!hasUsableVideo || creating}
            leadingIcon={
              creating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              )
            }
          >
            {t("edit_create_timeline")}
          </SecondaryButton>
        </div>
        {blockedReason !== undefined && (
          <p className="text-[12px]" style={{ color: "var(--color-text-4)" }}>
            {blockedReason}
          </p>
        )}
      </div>
    </div>
  );
}
