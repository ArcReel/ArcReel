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
  /** Disable while tier actions (model selection/unbinding) are pending. */
  busy?: boolean;
  /** Select model entries for tiers. */
  onBind: (tier: AgentCredentialTier, entry: CatalogEntryHit) => void;
  /** Unbind tiers, returning to automatic matching by model name. */
  onUnbind: (tier: AgentCredentialTier) => void;
}

/**
 * Actual models/pricing states for Agent-provider tiers. Each row has tier, model name, matching model entry or Unpriced, then tier actions;
 * rows are subgrids of outer grids. Unpriced tiers offer Select model; manually selected tiers (including removed models) offer Unbind.
 */
export function CredentialTierPricing({ tiers, busy = false, onBind, onUnbind }: Props) {
  const { t } = useTranslation("dashboard");
  const [pickingTier, setPickingTier] = useState<AgentCredentialTier | null>(null);
  return (
    <section aria-label={t("cred_tier_pricing_title")} className="flex flex-col gap-1.5 border-t border-border pt-3">
      <h4 className="text-xs font-medium text-muted-foreground">{t("cred_tier_pricing_title")}</h4>
      <ul className="grid grid-cols-[auto_minmax(0,1fr)_minmax(0,auto)_auto] gap-x-4 gap-y-1 text-xs">
        {tiers.map((status) => {
          // Existing bindings, including removed-model bindings, require unbind entries so users can clear them.
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
      {/* Selection-panel headings render at component locations only while open, leaving no invisible headings in closed lists. */}
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
  // Claude Code chooses models for empty tiers; accounting matches actual model names to entries, leaving no status to show here.
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
