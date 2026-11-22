import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface PolicyNoticeSpec {
  textKey: string;
  linkLabelKey: string;
  termsUrl: string;
}

const ARK_AGENT_PLAN_TERMS_URL =
  "https://docs.volcengine.com/docs/ark/subscription-package-service-terms-conditions?lang=zh";

export const PROVIDER_POLICY_NOTICES: Readonly<Record<string, PolicyNoticeSpec>> = {
  "ark-agent-plan": {
    textKey: "provider_policy_notice_ark_agent_plan",
    linkLabelKey: "provider_policy_terms_link",
    termsUrl: ARK_AGENT_PLAN_TERMS_URL,
  },
};

export const AGENT_PRESET_POLICY_NOTICES: Readonly<Record<string, PolicyNoticeSpec>> = {
  "ark-agent-plan": {
    textKey: "agent_preset_policy_notice_ark_agent_plan",
    linkLabelKey: "provider_policy_terms_link",
    termsUrl: ARK_AGENT_PLAN_TERMS_URL,
  },
};

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
