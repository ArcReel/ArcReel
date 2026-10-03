import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MarketSubmission, MarketSubmissionStatus } from "@/types";

const BADGE_CLS =
  "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em]";

const STATUS_CLS: Record<MarketSubmissionStatus, string> = {
  open: "border-primary/35 bg-primary/12 text-primary",
  merged: "border-good/35 bg-good/10 text-good",
  closed: "border-border/50 bg-card/55 text-muted-foreground",
};

const STATUS_KEY: Record<MarketSubmissionStatus, string> = {
  open: "market_submission_status_open",
  merged: "market_submission_status_merged",
  closed: "market_submission_status_closed",
};

/** 分享提交的状态徽标与 PR 链接；没能取回最新状态时在提示里注明展示的是上次状态。 */
export function MarketSubmissionBadge({ submission }: { submission: MarketSubmission }) {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`${BADGE_CLS} ${STATUS_CLS[submission.status]}`}
        title={submission.stale ? t("market_submission_stale") : undefined}
      >
        {t(STATUS_KEY[submission.status])}
      </span>
      <a
        href={submission.pr_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-0.5 text-[11.5px] text-muted-foreground hover:text-foreground"
      >
        {t("market_submission_pr_link")}
        <ExternalLink className="h-3 w-3" aria-hidden />
      </a>
    </span>
  );
}
