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
  /** Current revision issues; disable the button when any issue blocks all deliverables. */
  issues: readonly EditTimelineIssueRef[];
  /** Narration variants are selectable for TTS-voiceover projects. */
  narrationAvailable: boolean;
  /** Open the editing view issues list. */
  onShowIssues: () => void;
}

/**
 * Render button atop editing view opens the selected timeline tab's render dialog.
 *
 * Disable when current issues block all deliverables; hover explains why and an adjacent link opens issues.
 * Dialogs handle narration-only blocking issues according to the selected narration variant.
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
      {/* Disabled buttons do not trigger hover; attach reasons to their outer container. */}
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
