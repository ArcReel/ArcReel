import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { PromptTemplateDetail } from "@/types";
import { errMsg } from "@/utils/async";
import { flattenOutputSchema } from "./promptTemplateSchema";
import {
  categoryLabel,
  CHIP_CLS,
  DetailSection,
  ErrorCard,
  LoadingCard,
  LockBadge,
  type Load,
} from "./promptTemplateShared";
import { PromptTemplateSource } from "./PromptTemplateSource";

export function PromptTemplateDetailView({
  templateId,
  onBack,
  onOpenPartial,
}: {
  templateId: string;
  onBack: () => void;
  onOpenPartial: (name: string) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [state, setState] = useState<Load<PromptTemplateDetail>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    API.getPromptTemplate(templateId, { signal: controller.signal }).then(
      (detail) => {
        if (!controller.signal.aborted) setState({ status: "ready", data: detail });
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setState({ status: "error", message: errMsg(err) });
      },
    );
    return () => controller.abort();
  }, [templateId, attempt]);

  const retry = () => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  };

  return (
    <div className="flex flex-col items-start gap-6">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ChevronLeft aria-hidden />
        {t("dashboard:prompt_templates_back")}
      </Button>

      {state.status === "loading" && <LoadingCard label={t("dashboard:prompt_templates_loading")} />}
      {state.status === "error" && (
        <ErrorCard
          title={t("dashboard:prompt_templates_detail_load_failed")}
          message={state.message}
          onRetry={retry}
        />
      )}
      {state.status === "ready" && <DetailBody detail={state.data} onOpenPartial={onOpenPartial} />}
    </div>
  );
}

function DetailBody({
  detail,
  onOpenPartial,
}: {
  detail: PromptTemplateDetail;
  onOpenPartial: (name: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const { template, source, partials, output_schema: outputSchema } = detail;
  const axes = Object.entries(template.applies_to);
  const slots = Object.entries(template.slots);
  const schemaRows = useMemo(
    () => (outputSchema ? flattenOutputSchema(outputSchema) : []),
    [outputSchema],
  );

  return (
    <div className="flex w-full flex-col gap-6">
      <header className="flex flex-col gap-1">
        <p className="text-xs break-all text-muted-foreground">
          {categoryLabel(t, template.category)} · <span className="font-mono">{template.id}</span>
        </p>
        <div className="flex flex-wrap items-center gap-2.5">
          <h2 className="text-lg font-medium">{template.title}</h2>
          {template.protected && <LockBadge />}
        </div>
        <p className="max-w-prose text-sm text-muted-foreground">{template.description}</p>
      </header>

      <DetailSection title={t("prompt_templates_applies_to")}>
        {axes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("prompt_templates_applies_to_all")}</p>
        ) : (
          <dl className="flex flex-col gap-2.5">
            {axes.map(([axis, values]) => (
              <div key={axis} className="flex flex-wrap items-baseline gap-x-4 gap-y-1.5">
                <dt className="w-28 shrink-0 text-xs text-muted-foreground">
                  {t(`prompt_templates_axis_${axis}`, { defaultValue: axis })}
                </dt>
                <dd className="flex min-w-0 flex-1 flex-wrap gap-1.5">
                  {values.map((value) => (
                    <code key={value} className={CHIP_CLS}>
                      {value}
                    </code>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </DetailSection>

      <DetailSection title={t("prompt_templates_slots")} description={t("prompt_templates_slots_desc")}>
        {slots.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("prompt_templates_slots_empty")}</p>
        ) : (
          <dl className="@container flex flex-col gap-2">
            {slots.map(([name, description]) => (
              <div key={name} className="grid gap-x-4 gap-y-0.5 @md:grid-cols-[minmax(0,14rem)_1fr]">
                <dt className="font-mono text-xs break-all text-primary">{name}</dt>
                <dd className="text-sm text-subtle-foreground">{description}</dd>
              </div>
            ))}
          </dl>
        )}
      </DetailSection>

      <DetailSection title={t("prompt_templates_source")} description={t("prompt_templates_source_desc")}>
        <PromptTemplateSource text={source} template={template} partials={partials} onOpenPartial={onOpenPartial} />
      </DetailSection>

      {outputSchema && (
        <Collapsible render={<section />}>
          <div className="flex flex-col gap-1">
            <h3>
              <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2.5" />}>
                <ChevronRight
                  aria-hidden
                  className="text-muted-foreground motion-safe:transition-transform group-aria-expanded/button:rotate-90"
                />
                {t("prompt_templates_output_schema")}
              </CollapsibleTrigger>
            </h3>
            <p className="text-sm text-muted-foreground">{t("prompt_templates_output_schema_desc")}</p>
          </div>
          <CollapsibleContent className="mt-3">
            <div className="overflow-x-auto rounded-lg border border-border bg-card px-4 pb-4">
              <table className="mt-3 w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="py-1.5 pr-4 font-normal">{t("prompt_templates_schema_path")}</th>
                    <th className="py-1.5 pr-4 font-normal">{t("prompt_templates_schema_type")}</th>
                    <th className="py-1.5 font-normal">{t("prompt_templates_schema_description")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {schemaRows.map((row) => (
                    <tr key={row.path} className="align-top">
                      <td className="py-2 pr-4 font-mono">{row.path}</td>
                      <td className="py-2 pr-4">
                        <div className="flex flex-wrap items-center gap-1">
                          <code className="font-mono text-subtle-foreground">{row.type}</code>
                          {row.enumValues.map((value) => (
                            <code key={value} className={CHIP_CLS}>
                              {value}
                            </code>
                          ))}
                          {row.nullable && (
                            <span className="text-muted-foreground">{t("prompt_templates_schema_nullable")}</span>
                          )}
                        </div>
                      </td>
                      <td className="py-2 text-muted-foreground">{row.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}
