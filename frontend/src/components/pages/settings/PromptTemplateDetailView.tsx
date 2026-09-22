import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { SectionShell } from "@/components/ui/SectionShell";
import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type { PromptTemplateDetail } from "@/types";
import { errMsg } from "@/utils/async";
import { flattenOutputSchema } from "./promptTemplateSchema";
import { categoryLabel, ErrorCard, LoadingCard, type Load } from "./promptTemplateShared";
import { PromptTemplateSource } from "./PromptTemplateSource";

export function PromptTemplateDetailView({
  templateId,
  onBack,
}: {
  templateId: string;
  onBack: () => void;
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
    <section className="space-y-6">
      <button type="button" onClick={onBack} className={GHOST_BTN_CLS}>
        <ChevronLeft aria-hidden className="h-3.5 w-3.5" />
        {t("dashboard:prompt_templates_back")}
      </button>

      {state.status === "loading" && <LoadingCard label={t("dashboard:prompt_templates_loading")} />}
      {state.status === "error" && (
        <ErrorCard
          title={t("dashboard:prompt_templates_detail_load_failed")}
          message={state.message}
          onRetry={retry}
        />
      )}
      {state.status === "ready" && <DetailBody detail={state.data} />}
    </section>
  );
}

function DetailBody({ detail }: { detail: PromptTemplateDetail }) {
  const { t } = useTranslation("dashboard");
  const { template, source, partials, output_schema: outputSchema } = detail;
  const axes = Object.entries(template.applies_to);
  const slots = Object.entries(template.slots);
  const schemaRows = useMemo(
    () => (outputSchema ? flattenOutputSchema(outputSchema) : []),
    [outputSchema],
  );

  return (
    <>
      <header>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          {categoryLabel(t, template.category)} · {template.id}
        </div>
        <h2 className="font-editorial mt-1 text-[24px] leading-tight text-text">{template.title}</h2>
        <p className="mt-1.5 max-w-[62ch] text-[12.5px] leading-[1.6] text-text-3">
          {template.description}
        </p>
      </header>

      <SectionShell kicker="Applies To" title={t("prompt_templates_applies_to")}>
        {axes.length === 0 ? (
          <p className="text-[12.5px] text-text-3">{t("prompt_templates_applies_to_all")}</p>
        ) : (
          <dl className="space-y-2.5">
            {axes.map(([axis, values]) => (
              <div key={axis} className="flex flex-wrap items-baseline gap-x-4 gap-y-1.5">
                <dt className="w-28 shrink-0 text-[12px] text-text-3">
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
      </SectionShell>

      <SectionShell
        kicker="Slots"
        title={t("prompt_templates_slots")}
        description={t("prompt_templates_slots_desc")}
      >
        {slots.length === 0 ? (
          <p className="text-[12.5px] text-text-3">{t("prompt_templates_slots_empty")}</p>
        ) : (
          <dl className="space-y-2">
            {slots.map(([name, description]) => (
              <div key={name} className="grid gap-x-4 gap-y-0.5 sm:grid-cols-[minmax(0,14rem)_1fr]">
                <dt className="font-mono text-[12px] text-accent-2">{name}</dt>
                <dd className="text-[12.5px] leading-[1.55] text-text-2">{description}</dd>
              </div>
            ))}
          </dl>
        )}
      </SectionShell>

      <SectionShell
        kicker="Template Body"
        title={t("prompt_templates_source")}
        description={t("prompt_templates_source_desc")}
      >
        <PromptTemplateSource text={source} template={template} partials={partials} />
      </SectionShell>

      {outputSchema && (
        <details
          className="group rounded-[10px] border border-hairline"
          style={CARD_STYLE}
        >
          <summary className="flex cursor-pointer list-none items-start gap-2.5 rounded-[10px] p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-4 motion-safe:transition-transform group-open:rotate-90"
            />
            <span className="min-w-0 flex-1">
              <span className="block font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
                Output Schema
              </span>
              <span className="mt-1 block text-[14.5px] font-medium text-text">
                {t("prompt_templates_output_schema")}
              </span>
              <span className="mt-1 block text-[12px] leading-[1.55] text-text-3">
                {t("prompt_templates_output_schema_desc")}
              </span>
            </span>
          </summary>
          <div className="overflow-x-auto border-t border-hairline-soft px-4 pb-4">
            <table className="mt-3 w-full border-collapse text-left text-[12px]">
              <thead>
                <tr className="text-text-4">
                  <th className="py-1.5 pr-4 font-normal">{t("prompt_templates_schema_path")}</th>
                  <th className="py-1.5 pr-4 font-normal">{t("prompt_templates_schema_type")}</th>
                  <th className="py-1.5 font-normal">{t("prompt_templates_schema_description")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline-soft">
                {schemaRows.map((row) => (
                  <tr key={row.path} className="align-top">
                    <td className="py-2 pr-4 font-mono text-[11.5px] text-text">{row.path}</td>
                    <td className="py-2 pr-4">
                      <div className="flex flex-wrap items-center gap-1">
                        <code className="font-mono text-[11.5px] text-text-2">{row.type}</code>
                        {row.enumValues.map((value) => (
                          <code key={value} className={CHIP_CLS}>
                            {value}
                          </code>
                        ))}
                        {row.nullable && (
                          <span className="text-[11px] text-text-4">
                            {t("prompt_templates_schema_nullable")}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-2 leading-[1.55] text-text-3">{row.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </>
  );
}

const CHIP_CLS =
  "rounded-[5px] border border-hairline-soft bg-bg-grad-a/55 px-1.5 py-0.5 font-mono text-[11px] text-text-2";
