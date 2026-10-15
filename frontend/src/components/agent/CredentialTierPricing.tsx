import { useTranslation } from "react-i18next";

import { TruncatedText } from "@/components/shared/TruncatedText";
import type { AgentCredentialTier, AgentCredentialTierStatus } from "@/types/agent-credential";

const TIER_LABEL_KEY: Record<AgentCredentialTier, string> = {
  model: "default_model",
  haiku: "haiku_model",
  sonnet: "sonnet_model",
  opus: "opus_model",
  subagent: "subagent_model",
};

/**
 * Agent 供应商各档位实际调用的模型与定价状态。每行依次是档位、模型名、对应的模型条目或「未定价」；
 * 行是外层网格的子网格，末列留给档位操作（选择模型、解除绑定）。
 */
export function CredentialTierPricing({ tiers }: { tiers: AgentCredentialTierStatus[] }) {
  const { t } = useTranslation("dashboard");
  return (
    <section aria-label={t("cred_tier_pricing_title")} className="flex flex-col gap-1.5 border-t border-border pt-3">
      <h4 className="text-xs font-medium text-muted-foreground">{t("cred_tier_pricing_title")}</h4>
      <ul className="grid grid-cols-[auto_minmax(0,1fr)_minmax(0,auto)_auto] gap-x-4 gap-y-1 text-xs">
        {tiers.map((status) => (
          <li
            key={status.tier}
            aria-label={t(TIER_LABEL_KEY[status.tier])}
            className="col-span-full grid grid-cols-subgrid items-center"
          >
            <span className="text-muted-foreground">{t(TIER_LABEL_KEY[status.tier])}</span>
            {status.model_name ? (
              <TruncatedText text={status.model_name} className="font-mono" />
            ) : (
              <span className="truncate text-muted-foreground">{t("cred_tier_cli_default")}</span>
            )}
            <TierPricingState status={status} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function TierPricingState({ status }: { status: AgentCredentialTierStatus }) {
  const { t } = useTranslation("dashboard");
  // 档位留空时由 Claude Code 决定调用的模型，记账时按实际调用的模型名对应条目，这里没有可显示的状态。
  if (status.model_name === null) return <span />;
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      {status.priced ? (
        <span className="shrink-0 text-good">{t("cred_tier_priced")}</span>
      ) : (
        <span className="shrink-0 text-warn">{t("cred_tier_unpriced")}</span>
      )}
      {status.entry_display_name && (
        <TruncatedText text={status.entry_display_name} className="text-subtle-foreground" />
      )}
      {status.manually_bound && (
        <span className="shrink-0 text-muted-foreground">{t("cred_tier_manually_bound")}</span>
      )}
    </span>
  );
}
