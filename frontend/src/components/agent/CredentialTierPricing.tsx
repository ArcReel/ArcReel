import { useState } from "react";
import { useTranslation } from "react-i18next";

import { CatalogEntryPicker } from "@/components/shared/CatalogEntryPicker";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import type { CatalogEntryHit } from "@/types";
import type { AgentCredentialTier, AgentCredentialTierStatus } from "@/types/agent-credential";

const TIER_LABEL_KEY: Record<AgentCredentialTier, string> = {
  model: "default_model",
  haiku: "haiku_model",
  sonnet: "sonnet_model",
  opus: "opus_model",
  subagent: "subagent_model",
};

interface Props {
  tiers: AgentCredentialTierStatus[];
  /** 档位操作（选择模型、解除绑定）请求在途时禁用。 */
  busy?: boolean;
  /** 为档位选定模型条目。 */
  onBind: (tier: AgentCredentialTier, entry: CatalogEntryHit) => void;
  /** 解除档位的绑定，回到按模型名自动对应。 */
  onUnbind: (tier: AgentCredentialTier) => void;
}

/**
 * Agent 供应商各档位实际调用的模型与定价状态。每行依次是档位、模型名、对应的模型条目或「未定价」、档位操作；
 * 行是外层网格的子网格。未定价的档位可以「选择模型」，手动选择过的档位（含所选模型已下架的）可以「解除绑定」。
 */
export function CredentialTierPricing({ tiers, busy = false, onBind, onUnbind }: Props) {
  const { t } = useTranslation("dashboard");
  const [pickingTier, setPickingTier] = useState<AgentCredentialTier | null>(null);
  return (
    <section aria-label={t("cred_tier_pricing_title")} className="flex flex-col gap-1.5 border-t border-border pt-3">
      <h4 className="text-xs font-medium text-muted-foreground">{t("cred_tier_pricing_title")}</h4>
      <ul className="grid grid-cols-[auto_minmax(0,1fr)_minmax(0,auto)_auto] gap-x-4 gap-y-1 text-xs">
        {tiers.map((status) => {
          // 绑定还存着（所选模型已下架的也算）就得有解除的入口，否则用户清不掉
          const canUnbind = status.manually_bound || status.binding_stale;
          const canPick = status.model_name !== null && !status.priced;
          return (
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
              <span className="flex items-center justify-end gap-1">
                {canPick && (
                  <Button variant="outline" size="xs" disabled={busy} onClick={() => setPickingTier(status.tier)}>
                    {t("cred_tier_pick_model")}
                  </Button>
                )}
                {canUnbind && (
                  <Button variant="ghost" size="xs" disabled={busy} onClick={() => onUnbind(status.tier)}>
                    {t("cred_tier_unbind")}
                  </Button>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {/* 选择面板的标题行渲染在组件所在位置，只在打开时挂载，关闭时不在列表里留不可见的标题 */}
      {pickingTier !== null && (
        <CatalogEntryPicker
          open
          onOpenChange={(open) => {
            if (!open) setPickingTier(null);
          }}
          mediaType="text"
          title={t("cred_tier_pick_title", { tier: t(TIER_LABEL_KEY[pickingTier]) })}
          onSelect={(entry) => onBind(pickingTier, entry)}
        />
      )}
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
      {status.binding_stale && <span className="shrink-0 text-warn">{t("cred_tier_binding_stale")}</span>}
    </span>
  );
}
