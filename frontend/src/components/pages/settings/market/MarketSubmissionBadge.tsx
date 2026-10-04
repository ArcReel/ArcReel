import { Check, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { MarketSubmission, MarketSubmissionStatus } from "@/types";

const STATUS_KEY: Record<MarketSubmissionStatus, string> = {
  open: "market_submission_status_open",
  merged: "market_submission_status_merged",
  closed: "market_submission_status_closed",
};

/** 分享提交的状态徽标与 PR 链接；没能取回最新状态时在徽标旁注明展示的是上次状态。 */
export function MarketSubmissionBadge({ submission }: { submission: MarketSubmission }) {
  const { t } = useTranslation("dashboard");
  const label = t(STATUS_KEY[submission.status]);
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      {submission.status === "merged" ? (
        <Badge variant="outline">
          <Check data-icon="inline-start" className="text-good" aria-hidden />
          {label}
        </Badge>
      ) : (
        <Badge variant={submission.status === "open" ? "outline" : "secondary"}>{label}</Badge>
      )}
      {submission.stale && <span className="text-xs text-muted-foreground">{t("market_submission_stale")}</span>}
      <a
        href={submission.pr_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline"
      >
        {t("market_submission_pr_link")}
        <ExternalLink className="size-3.5" aria-hidden />
      </a>
    </span>
  );
}
