import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Clapperboard } from "lucide-react";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import type { EditTimelineIssueRef } from "@/types";
import { formatNameList } from "@/utils/list-format";
import { blockingIssues, blockingUnitIds } from "./edit-render-model";
import { RenderDialog } from "./RenderDialog";
import { itemIdWithinEpisode } from "@/utils/episode-display";

interface RenderButtonProps {
  projectName: string;
  timelineId: string;
  timelineName: string;
  /** 当前修订的 issues；有阻断级 issue 时按钮不可点。 */
  issues: readonly EditTimelineIssueRef[];
  /** 打开剪辑视图的「问题」列表。 */
  onShowIssues: () => void;
}

/**
 * 剪辑视图顶部的「出片」按钮，打开当前标签对应剪辑时间线的出片对话框。
 *
 * 当前修订有阻断级 issue 时按钮不可点：悬停显示原因，旁边给出跳到「问题」列表的链接。
 */
export function RenderButton({ projectName, timelineId, timelineName, issues, onShowIssues }: RenderButtonProps) {
  const { t, i18n } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const reasonId = useId();
  const blocking = blockingIssues(issues);
  const units = blockingUnitIds(blocking);
  let reason: string | null = null;
  if (blocking.length > 0) {
    reason =
      units.length > 0
        ? t("edit_render_blocked_reason_units", {
            count: blocking.length,
            units: formatNameList(units.map(itemIdWithinEpisode), i18n.language),
          })
        : t("edit_render_blocked_reason", { count: blocking.length });
  }

  return (
    <div className="flex items-center gap-2">
      {reason !== null && (
        <>
          <button
            type="button"
            onClick={onShowIssues}
            className="focus-ring rounded text-[12px] underline decoration-dotted underline-offset-2"
            style={{ color: "var(--color-danger)" }}
            title={reason}
          >
            {t("edit_render_blocked_view_issues", { count: blocking.length })}
          </button>
          <span id={reasonId} className="sr-only">
            {reason}
          </span>
        </>
      )}
      {/* 禁用的 button 不触发悬停，原因挂在外层容器上。 */}
      <span title={reason ?? undefined}>
        <PrimaryButton
          tone="accent"
          size="sm"
          onClick={() => setOpen(true)}
          disabled={reason !== null}
          aria-describedby={reason !== null ? reasonId : undefined}
          leadingIcon={<Clapperboard className="h-3.5 w-3.5" aria-hidden="true" />}
        >
          {t("edit_render_button")}
        </PrimaryButton>
      </span>
      <RenderDialog
        open={open}
        onClose={() => setOpen(false)}
        projectName={projectName}
        timelineId={timelineId}
        timelineName={timelineName}
        blockedReason={reason}
      />
    </div>
  );
}
