import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Clapperboard } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { EditTimelineIssueRef } from "@/types";
import { RenderDialog } from "./RenderDialog";
import { useBlockedReason } from "./useBlockedReason";

interface RenderButtonProps {
  projectName: string;
  timelineId: string;
  timelineName: string;
  /** 当前修订的 issues；有阻断全部交付物的 issue 时按钮不可点。 */
  issues: readonly EditTimelineIssueRef[];
  /** 项目用 TTS 配音时可以选旁白版本。 */
  narrationAvailable: boolean;
  /** 打开剪辑视图的「问题」列表。 */
  onShowIssues: () => void;
}

/**
 * 剪辑视图顶部的「出片」按钮，打开当前标签对应剪辑时间线的出片对话框。
 *
 * 当前修订有阻断全部交付物的 issue 时按钮不可点：悬停显示原因，旁边给出跳到「问题」列表的链接。
 * 只阻断带旁白版本的 issue 由对话框按所选旁白版本处理。
 */
export function RenderButton({
  projectName,
  timelineId,
  timelineName,
  issues,
  narrationAvailable,
  onShowIssues,
}: RenderButtonProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const reasonId = useId();
  const { count, reason } = useBlockedReason(issues);

  return (
    <div className="flex items-center gap-2">
      {reason !== null && (
        <>
          <Button variant="link" size="sm" onClick={onShowIssues} title={reason}>
            <AlertTriangle data-icon="inline-start" className="text-destructive" aria-hidden />
            {t("edit_render_blocked_view_issues", { count })}
          </Button>
          <span id={reasonId} className="sr-only">
            {reason}
          </span>
        </>
      )}
      {/* 禁用的 button 不触发悬停，原因挂在外层容器上。 */}
      <span title={reason ?? undefined}>
        <Button
          size="sm"
          onClick={() => setOpen(true)}
          disabled={reason !== null}
          aria-describedby={reason !== null ? reasonId : undefined}
        >
          <Clapperboard data-icon="inline-start" aria-hidden />
          {t("edit_render_button")}
        </Button>
      </span>
      <RenderDialog
        open={open}
        onClose={() => setOpen(false)}
        projectName={projectName}
        timelineId={timelineId}
        timelineName={timelineName}
        issues={issues}
        narrationAvailable={narrationAvailable}
      />
    </div>
  );
}
