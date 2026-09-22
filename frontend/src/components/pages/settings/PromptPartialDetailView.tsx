import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { SectionShell } from "@/components/ui/SectionShell";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type { PromptTemplateMeta, PromptTemplatePartial } from "@/types";
import { errMsg } from "@/utils/async";
import { categoryLabel, ErrorCard, LoadingCard, LockBadge, type Load } from "./promptTemplateShared";

type PartialDetail = {
  partial: PromptTemplatePartial;
  /** 模版 id → 元数据，用于把引用方显示为标题；列表加载失败时为空，退回显示 id。 */
  templates: Map<string, PromptTemplateMeta>;
};

export function PromptPartialDetailView({
  name,
  backTo,
  onBack,
  onOpenTemplate,
}: {
  name: string;
  backTo: "template" | "list";
  onBack: () => void;
  onOpenTemplate: (id: string) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [state, setState] = useState<Load<PartialDetail>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    Promise.all([
      API.getPromptPartial(name, { signal }),
      API.listPromptTemplates({ signal }).then(
        (response) => response.templates,
        () => [],
      ),
    ]).then(
      ([partial, templates]) => {
        if (!signal.aborted) {
          setState({
            status: "ready",
            data: { partial, templates: new Map(templates.map((item) => [item.id, item])) },
          });
        }
      },
      (err: unknown) => {
        if (!signal.aborted) setState({ status: "error", message: errMsg(err) });
      },
    );
    return () => controller.abort();
  }, [name, attempt]);

  const retry = () => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  };

  return (
    <section className="space-y-6">
      <button type="button" onClick={onBack} className={GHOST_BTN_CLS}>
        <ChevronLeft aria-hidden className="h-3.5 w-3.5" />
        {t(backTo === "template" ? "dashboard:prompt_templates_back_to_template" : "dashboard:prompt_templates_back")}
      </button>

      {state.status === "loading" && <LoadingCard label={t("dashboard:prompt_templates_loading")} />}
      {state.status === "error" && (
        <ErrorCard
          title={t("dashboard:prompt_templates_partial_load_failed")}
          message={state.message}
          onRetry={retry}
        />
      )}
      {state.status === "ready" && <PartialBody detail={state.data} onOpenTemplate={onOpenTemplate} />}
    </section>
  );
}

function PartialBody({
  detail: { partial, templates },
  onOpenTemplate,
}: {
  detail: PartialDetail;
  onOpenTemplate: (id: string) => void;
}) {
  const { t } = useTranslation("dashboard");

  return (
    <>
      <header>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          {t("prompt_templates_partial")}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2.5">
          <h2 className="break-all font-mono text-[20px] leading-tight text-text">{partial.name}</h2>
          {partial.protected && <LockBadge />}
        </div>
        <p className="mt-1.5 text-[12.5px] text-text-3">
          {t("prompt_templates_referenced_count", { count: partial.referenced_by.length })}
        </p>
      </header>

      <SectionShell kicker="Partial Body" title={t("prompt_templates_partial_source")}>
        {partial.source.trim() ? (
          <div className="overflow-x-auto whitespace-pre-wrap break-words rounded-[8px] border border-hairline-soft bg-bg-grad-b/60 px-3.5 py-3 font-mono text-[12px] leading-[1.7] text-text-2">
            {partial.source}
          </div>
        ) : (
          <p className="text-[12.5px] italic text-text-4">{t("prompt_templates_partial_blank")}</p>
        )}
      </SectionShell>

      <SectionShell
        kicker="Used By"
        title={t("prompt_templates_referenced_by")}
        description={t("prompt_templates_referenced_by_desc")}
      >
        <ul className="divide-y divide-hairline-soft">
          {partial.referenced_by.map((id) => {
            const template = templates.get(id);
            return (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onOpenTemplate(id)}
                  className="group flex w-full items-center gap-3 rounded-[6px] px-1 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] text-text group-hover:underline">
                      {template?.title ?? id}
                    </span>
                    <span className="mt-0.5 block font-mono text-[11px] text-text-4">
                      {template ? `${categoryLabel(t, template.category)} · ${id}` : id}
                    </span>
                  </span>
                  {template?.protected && <LockBadge />}
                  <ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-text-4" />
                </button>
              </li>
            );
          })}
        </ul>
      </SectionShell>
    </>
  );
}
