import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight, Bot, CheckCircle2, Clock, OctagonAlert, RotateCcw } from "lucide-react";
import { cn } from "cn";
import type { DraftSoftViolation, ScriptReviewViolation } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { draftViolationMessage } from "@/utils/draft-violations";
import { withUniqueKeys, type ReviewItemJump } from "./script-review-diagnostics";

interface ScriptReviewStatusHeaderProps {
  confirmed: boolean;
  scriptMissing: boolean;
  overwrite: boolean;
  blockingCount: number;
  itemJumps: ReviewItemJump[];
  episodeLevelCount: number;
  onJump: (index: number) => void;
  onJumpEpisodeLevel: () => void;
  dirty: boolean;
  outdated: boolean;
  busy: boolean;
  onReloadLatest: () => void;
  onHandToAgent?: () => void;
  onOpenTimeline?: () => void;
  regenerateAction: ReactNode;
  saveAction: ReactNode;
  confirmAction: ReactNode;
}

function StatusHeaderIcon({ hasBlocking, confirmed }: { hasBlocking: boolean; confirmed: boolean }) {
  if (hasBlocking) {
    return <OctagonAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-destructive" />;
  }
  if (confirmed) {
    return <CheckCircle2 aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-good" />;
  }
  return <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />;
}

function StatusHeaderSummary({
  hasBlocking,
  confirmed,
  scriptMissing,
  overwrite,
  itemJumps,
  episodeLevelCount,
  onJump,
  onJumpEpisodeLevel,
}: {
  hasBlocking: boolean;
  confirmed: boolean;
  scriptMissing: boolean;
  overwrite: boolean;
  itemJumps: ReviewItemJump[];
  episodeLevelCount: number;
  onJump: (index: number) => void;
  onJumpEpisodeLevel: () => void;
}) {
  const { t } = useTranslation("dashboard");
  if (hasBlocking) {
    const jumps = [
      ...(episodeLevelCount > 0
        ? [{ key: "episode", label: t("review_episode_level_count", { count: episodeLevelCount }), onClick: onJumpEpisodeLevel }]
        : []),
      ...itemJumps.map((jump) => ({
        key: `item-${String(jump.index)}`,
        label: `${jump.label} · ${String(jump.count)}`,
        onClick: () => onJump(jump.index),
      })),
    ];
    return (
      <>
        {jumps.map((jump, i) => (
          <span key={jump.key}>
            {i > 0 && ", "}
            <button
              type="button"
              onClick={jump.onClick}
              className="rounded-sm text-destructive underline decoration-destructive/40 underline-offset-2 focus-ring hover:decoration-destructive"
            >
              {jump.label}
            </button>
          </span>
        ))}
        {jumps.length > 0 ? <span> — {t("review_blocking_hint")}</span> : t("review_blocking_hint")}
      </>
    );
  }
  if (scriptMissing) return t("review_script_missing_hint");
  if (confirmed) return t("review_confirmed_hint");
  if (overwrite) return t("review_overwrite_hint");
  return t("review_pending_hint");
}

function StatusHeaderBanner({
  outdated,
  hasBlocking,
  dirty,
  busy,
  onReloadLatest,
}: {
  outdated: boolean;
  hasBlocking: boolean;
  dirty: boolean;
  busy: boolean;
  onReloadLatest: () => void;
}) {
  const { t } = useTranslation("dashboard");
  if (outdated) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-warn">
        <AlertTriangle className="size-3 shrink-0" aria-hidden="true" />
        <span className="wrap-break-word">{t("review_outdated_hint")}</span>
        <Button variant="link" size="xs" onClick={onReloadLatest} disabled={busy}>
          <RotateCcw aria-hidden data-icon="inline-start" />
          {t("review_reload_latest")}
        </Button>
      </div>
    );
  }
  if (hasBlocking && dirty) {
    return <p className="text-xs text-muted-foreground wrap-break-word">{t("review_dirty_agent_hint")}</p>;
  }
  return null;
}

export function ScriptReviewStatusHeader({
  confirmed,
  scriptMissing,
  overwrite,
  blockingCount,
  itemJumps,
  episodeLevelCount,
  onJump,
  onJumpEpisodeLevel,
  dirty,
  outdated,
  busy,
  onReloadLatest,
  onHandToAgent,
  onOpenTimeline,
  regenerateAction,
  saveAction,
  confirmAction,
}: ScriptReviewStatusHeaderProps) {
  const { t } = useTranslation("dashboard");
  const hasBlocking = blockingCount > 0;

  return (
    <header
      className={cn(
        "sticky top-0 z-sticky flex flex-col gap-2 rounded-lg border bg-card px-3.5 py-2.5",
        hasBlocking ? "border-destructive/40" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="flex min-w-0 items-start gap-2">
          <StatusHeaderIcon hasBlocking={hasBlocking} confirmed={confirmed} />
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-sm font-medium text-foreground">
              {confirmed ? t("review_status_confirmed") : t("review_status_pending")}
              {hasBlocking && (
                <span className="ml-1.5 text-destructive">{t("review_blocking_count", { count: blockingCount })}</span>
              )}
            </span>
            <span className="text-xs text-muted-foreground wrap-break-word">
              <StatusHeaderSummary
                hasBlocking={hasBlocking}
                confirmed={confirmed}
                scriptMissing={scriptMissing}
                overwrite={overwrite}
                itemJumps={itemJumps}
                episodeLevelCount={episodeLevelCount}
                onJump={onJump}
                onJumpEpisodeLevel={onJumpEpisodeLevel}
              />
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {onOpenTimeline && (
            <Button variant="outline" size="sm" onClick={onOpenTimeline}>
              <ArrowRight aria-hidden data-icon="inline-start" />
              {t("review_open_timeline")}
            </Button>
          )}
          {regenerateAction}
          {hasBlocking && onHandToAgent && (
            <Button
              variant="outline"
              size="sm"
              onClick={onHandToAgent}
              disabled={busy || dirty}
              title={dirty ? t("review_dirty_agent_hint") : undefined}
            >
              <Bot aria-hidden data-icon="inline-start" />
              {t("review_hand_to_agent")}
            </Button>
          )}
          {saveAction}
          {confirmAction}
        </div>
      </div>

      <StatusHeaderBanner
        outdated={outdated}
        hasBlocking={hasBlocking}
        dirty={dirty}
        busy={busy}
        onReloadLatest={onReloadLatest}
      />
    </header>
  );
}

export function ReviewEpisodeBlockingIssues({
  issues,
  anchorRef,
}: {
  issues: ScriptReviewViolation[];
  anchorRef?: (el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  if (issues.length === 0) return null;
  return (
    <section
      ref={anchorRef}
      aria-label={t("review_episode_level_title")}
      className="scroll-mt-28 rounded-lg border border-destructive/45 bg-card p-3.5"
    >
      <h3 className="text-xs font-medium text-destructive">{t("review_episode_level_title")}</h3>
      <ReviewBlockingIssueList issues={issues} />
    </section>
  );
}

export function ReviewBlockingIssueList({
  issues,
  position,
}: {
  issues: ScriptReviewViolation[];
  position?: number | null;
}) {
  const { t } = useTranslation();
  if (issues.length === 0) return null;
  const keyedIssues = withUniqueKeys(
    issues,
    (issue) => `${issue.code}:${String(issue.item_index ?? "ep")}:${String(issue.line ?? "nl")}:${issue.message}`,
  );
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {keyedIssues.map(({ item: issue, key }) => (
        <li key={key} className="flex items-start gap-1.5 text-xs leading-snug text-destructive">
          <OctagonAlert className="mt-px size-3 shrink-0" aria-hidden="true" />
          <span className="wrap-break-word">{draftViolationMessage(issue, t, position)}</span>
        </li>
      ))}
    </ul>
  );
}

export function ReviewSoftViolationList({
  softViolations,
  position,
}: {
  softViolations: DraftSoftViolation[];
  position?: number | null;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  if (softViolations.length === 0) return null;
  const keyedViolations = withUniqueKeys(
    softViolations,
    (soft) => `${soft.code}:${String(soft.item_index)}:${soft.message}`,
  );
  return (
    <ul className="mt-2 flex flex-col gap-1">
      {keyedViolations.map(({ item: soft, key }) => (
        <li key={key} className="flex items-start gap-1.5 text-xs leading-snug text-warn">
          <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden="true" />
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
            <Badge variant="outline">{t("dashboard:review_soft_violation_badge")}</Badge>
            <span className="wrap-break-word">{draftViolationMessage(soft, t, position)}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
