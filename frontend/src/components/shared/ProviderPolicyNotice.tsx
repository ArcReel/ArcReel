import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PolicyNoticeSpec } from "./providerPolicyNotices";

export function ProviderPolicyNotice({ notice, className }: { notice: PolicyNoticeSpec; className?: string }) {
  const { t } = useTranslation("dashboard");
  return (
    <p className={className}>
      {t(notice.textKey)}{" "}
      <a
        href={notice.termsUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-0.5 underline underline-offset-2 hover:text-foreground"
      >
        {t(notice.linkLabelKey)}
        <ExternalLink aria-hidden className="size-3" />
      </a>
    </p>
  );
}
