import { CheckCircle, Loader2, Pencil, PlayCircle, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CatalogEntryHit } from "@/types";
import type { AgentCredential, AgentCredentialTier, TestConnectionResponse } from "@/types/agent-credential";

import { CredentialTierPricing } from "./CredentialTierPricing";
import { PresetIcon } from "./PresetIcon";
import { TestResultPanel } from "./TestResultPanel";

interface Props {
  credentials: AgentCredential[];
  busyId?: number | null;
  /** 显示在对应 Agent 供应商下方的连接测试结果(null 时不显示). */
  testedId?: number | null;
  testResult?: TestConnectionResponse | null;
  onActivate: (id: number) => void;
  onTest: (id: number) => void;
  onEdit: (cred: AgentCredential) => void;
  onDelete: (cred: AgentCredential) => void;
  /** 档位绑定或解除绑定请求在途的 Agent 供应商。 */
  tierBusyId?: number | null;
  onBindTier: (id: number, tier: AgentCredentialTier, entry: CatalogEntryHit) => void;
  onUnbindTier: (id: number, tier: AgentCredentialTier) => void;
}

export function CredentialList({
  credentials,
  busyId = null,
  testedId = null,
  testResult = null,
  onActivate,
  onTest,
  onEdit,
  onDelete,
  tierBusyId = null,
  onBindTier,
  onUnbindTier,
}: Props) {
  const { t } = useTranslation("dashboard");

  if (credentials.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        {t("cred_list_empty")}
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-3">
      {credentials.map((c) => {
        const busy = busyId === c.id;
        return (
          <li
            key={c.id}
            aria-label={c.display_name}
            className={cn(
              "flex flex-col gap-3 rounded-lg border bg-card p-4",
              c.is_active ? "border-primary/50" : "border-border",
            )}
          >
            <div className="flex items-start gap-3">
              <PresetIcon iconKey={c.icon_key} size={28} />
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <div className="flex min-w-0 items-center gap-2">
                  <TruncatedText text={c.display_name} className="text-sm font-medium" />
                  {c.is_active && (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-primary/15 px-1.5 py-0.5 text-xs text-primary">
                      <CheckCircle aria-hidden className="size-3" />
                      {t("is_active")}
                    </span>
                  )}
                </div>
                <TruncatedText text={c.base_url} className="font-mono text-xs text-muted-foreground" />
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button variant="outline" size="sm" onClick={() => onTest(c.id)} disabled={busy} aria-busy={busy}>
                  {busy ? (
                    <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                  ) : (
                    <PlayCircle aria-hidden data-icon="inline-start" />
                  )}
                  {busy ? t("cred_testing") : t("cred_test_label")}
                </Button>
                {!c.is_active && (
                  <Button variant="outline" size="sm" onClick={() => onActivate(c.id)} disabled={busy}>
                    {t("cred_activate_label")}
                  </Button>
                )}
                <Button variant="ghost" size="icon-sm" onClick={() => onEdit(c)} aria-label={t("cred_edit_label")}>
                  <Pencil aria-hidden />
                </Button>
                {c.is_active ? (
                  // 生效中的 Agent 供应商不能删除；禁用按钮收不到指针事件，提示挂在外层可聚焦的包裹上。
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 包裹禁用的删除按钮，须能用键盘聚焦以读到不能删除的原因
                        <span tabIndex={0} className="inline-flex rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50" />
                      }
                    >
                      <Button variant="ghost" size="icon-sm" disabled aria-label={t("cred_delete_label")}>
                        <Trash2 aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{t("cred_delete_active_blocked")}</TooltipContent>
                  </Tooltip>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => onDelete(c)}
                    disabled={busy}
                    aria-label={t("cred_delete_label")}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                )}
              </div>
            </div>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 border-t border-border pt-3 text-xs @md/page:grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,1fr)]">
              <dt className="text-muted-foreground">{t("cred_api_key")}</dt>
              <dd className="min-w-0">
                <TruncatedText text={c.api_key_masked} className="font-mono" />
              </dd>
              <dt className="text-muted-foreground">{t("cred_multiplier_label")}</dt>
              <dd className="tabular-nums">×{c.multiplier}</dd>
            </dl>
            <CredentialTierPricing
              tiers={c.tiers}
              busy={tierBusyId === c.id}
              onBind={(tier, entry) => onBindTier(c.id, tier, entry)}
              onUnbind={(tier) => onUnbindTier(c.id, tier)}
            />
            {testedId === c.id && testResult && <TestResultPanel result={testResult} />}
          </li>
        );
      })}
    </ul>
  );
}
