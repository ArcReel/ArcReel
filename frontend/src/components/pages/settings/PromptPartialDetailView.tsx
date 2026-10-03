import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import type { PromptTemplateMeta, PromptTemplatePartial } from "@/types";
import { errMsg } from "@/utils/async";
import {
  categoryLabel,
  DetailSection,
  ErrorCard,
  LoadingCard,
  LockBadge,
  SOURCE_BLOCK_CLS,
  type Load,
} from "./promptTemplateShared";

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
    <div className="flex flex-col items-start gap-6">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ChevronLeft aria-hidden />
        {t(backTo === "template" ? "dashboard:prompt_templates_back_to_template" : "dashboard:prompt_templates_back")}
      </Button>

      {state.status === "loading" && <LoadingCard label={t("dashboard:prompt_templates_loading")} />}
      {state.status === "error" && (
        <ErrorCard
          title={t("dashboard:prompt_templates_partial_load_failed")}
          message={state.message}
          onRetry={retry}
        />
      )}
      {state.status === "ready" && <PartialBody detail={state.data} onOpenTemplate={onOpenTemplate} />}
    </div>
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
    <div className="flex w-full flex-col gap-6">
      <header className="flex flex-col gap-1">
        <p className="text-xs text-muted-foreground">{t("prompt_templates_partial")}</p>
        <div className="flex flex-wrap items-center gap-2.5">
          <h2 className="font-mono text-lg font-medium break-all">{partial.name}</h2>
          {partial.protected && <LockBadge />}
        </div>
        <p className="text-sm text-muted-foreground">
          {t("prompt_templates_referenced_count", { count: partial.referenced_by.length })}
        </p>
      </header>

      <DetailSection title={t("prompt_templates_partial_source")}>
        {partial.source.trim() ? (
          <div className={SOURCE_BLOCK_CLS}>{partial.source}</div>
        ) : (
          <p className="text-sm text-muted-foreground italic">{t("prompt_templates_partial_blank")}</p>
        )}
      </DetailSection>

      <DetailSection title={t("prompt_templates_referenced_by")} description={t("prompt_templates_referenced_by_desc")}>
        <ul className="-my-2 divide-y divide-border">
          {partial.referenced_by.map((id) => {
            const template = templates.get(id);
            return (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onOpenTemplate(id)}
                  className="group flex w-full items-center gap-3 rounded-sm py-2.5 text-left focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="text-sm group-hover:underline">{template?.title ?? id}</span>
                    <span className="font-mono text-xs break-all text-muted-foreground">
                      {template ? `${categoryLabel(t, template.category)} · ${id}` : id}
                    </span>
                  </span>
                  {template?.protected && <LockBadge />}
                  <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                </button>
              </li>
            );
          })}
        </ul>
      </DetailSection>
    </div>
  );
}
