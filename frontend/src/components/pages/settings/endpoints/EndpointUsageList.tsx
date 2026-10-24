import { useId } from "react";
import { Link } from "wouter";
import { useTranslation } from "react-i18next";
import { providerSettingsPath } from "@/app-routes";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { buttonVariants } from "@/components/ui/button";
import type { EndpointUsage } from "./endpoint-usages";

/** 端点详情的「使用这个端点的模型」：每行可跳回对应供应商并定位到模型。 */
export function EndpointUsageList({ usages }: { usages: EndpointUsage[] }) {
  const { t } = useTranslation("dashboard");
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h3 id={headingId} className="text-base font-medium">
        {t("ce_usage_title")}
      </h3>
      {usages.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("ce_usage_empty")}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {usages.map(({ provider, model }) => (
            <li key={`${provider.id}/${model.id}`} className="flex items-center gap-3 px-4 py-2">
              <span className="min-w-0 flex-1 font-mono text-sm" translate="no">
                <TruncatedText text={model.model_id} />
              </span>
              <span className="min-w-0 max-w-2/5 text-sm text-muted-foreground">
                <TruncatedText text={provider.display_name} />
              </span>
              <Link
                href={providerSettingsPath({ custom: provider.id, model: model.model_id })}
                aria-label={t("ce_usage_open_label", { model: model.model_id, provider: provider.display_name })}
                className={buttonVariants({ variant: "ghost", size: "sm" })}
              >
                {t("ce_usage_open")}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
