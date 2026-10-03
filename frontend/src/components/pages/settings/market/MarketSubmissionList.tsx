import { useTranslation } from "react-i18next";
import type { MarketSubmission } from "@/types";
import { KICKER_CLS } from "./market-source-status";
import { MarketSubmissionBadge } from "./MarketSubmissionBadge";

/** 市场页的「我的分享提交」：各端点最近一次提交的 slug、状态与 PR 链接，点端点名跳到端点页。 */
export function MarketSubmissionList({
  submissions,
  onOpenEndpoint,
}: {
  submissions: MarketSubmission[];
  onOpenEndpoint: (endpointKey: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <section aria-labelledby="market-submissions-title" className="mt-10">
      <div className={KICKER_CLS}>Submissions</div>
      <h3 id="market-submissions-title" className="mt-1 text-[14px] font-medium text-foreground">
        {t("market_submissions_title")}
      </h3>
      <ul className="mt-3 divide-y divide-border/50 rounded-lg border border-border">
        {submissions.map((submission) => (
          <li key={submission.endpoint_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
            <button
              type="button"
              onClick={() => onOpenEndpoint(submission.endpoint_key)}
              className="min-w-0 truncate text-left text-[12.5px] text-foreground hover:text-primary"
            >
              {submission.endpoint_display_name}
            </button>
            <span className="font-mono text-[11px] text-muted-foreground">{submission.slug}</span>
            <span className="ml-auto">
              <MarketSubmissionBadge submission={submission} />
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
