import { ExternalLink } from "lucide-react";
import { Link } from "wouter";
import { useTranslation } from "react-i18next";
import { endpointSettingsPath, marketSettingsPath } from "@/app-routes";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { buttonVariants } from "@/components/ui/button";
import type { MarketSubmission } from "@/types";
import { MARKET_CONTRIBUTING_URL } from "./market-links";
import { MarketSubmissionBadge } from "./MarketSubmissionBadge";

/**
 * My shares tab: the latest official-market submission for each endpoint, showing its name linked to the endpoint, slug, status and PR.
 * Share through the official service; when disabled, explain why and link to Settings. End with manual submission guidance.
 */
export function MarketSharedTab({
  officialEnabled,
  submissions,
}: {
  officialEnabled: boolean;
  /** null means the official service is disabled or still loading. */
  submissions: MarketSubmission[] | null;
}) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex max-w-190 flex-col gap-6">
      {!officialEnabled ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
          <p>{t("market_shared_requires_official")}</p>
          <Link href={marketSettingsPath("settings")} className={buttonVariants({ variant: "outline", size: "sm" })}>
            {t("market_open_settings")}
          </Link>
        </div>
      ) : submissions === null ? null : submissions.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
          {t("market_shared_empty")}
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {submissions.map((submission) => (
            <li key={submission.endpoint_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <Link
                  href={endpointSettingsPath(submission.endpoint_key)}
                  className="min-w-0 text-sm font-medium text-foreground underline-offset-4 hover:underline"
                >
                  <TruncatedText text={submission.endpoint_display_name} focusable={false} />
                </Link>
                <TruncatedText text={submission.slug} className="font-mono text-xs text-muted-foreground" />
              </div>
              <MarketSubmissionBadge submission={submission} />
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col items-start gap-2 text-sm text-muted-foreground">
        <p>{t("market_contribute_body")}</p>
        <a
          href={MARKET_CONTRIBUTING_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
        >
          {t("market_contribute_link")}
          <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </div>
    </div>
  );
}
