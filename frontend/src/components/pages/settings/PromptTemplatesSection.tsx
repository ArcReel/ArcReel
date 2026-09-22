import { useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { PromptTemplateMeta } from "@/types";
import { errMsg } from "@/utils/async";
import { PromptTemplateDetailView } from "./PromptTemplateDetailView";
import { categoryLabel, ErrorCard, LoadingCard, type Load } from "./promptTemplateShared";

/** 系统设置 › 提示词模版：按类别只读浏览随版本内置的模版。类别与轴以接口返回为准，页面不枚举。 */
export function PromptTemplatesSection() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (selectedId !== null) {
    return (
      <PromptTemplateDetailView
        key={selectedId}
        templateId={selectedId}
        onBack={() => setSelectedId(null)}
      />
    );
  }
  return <PromptTemplateList onSelect={setSelectedId} />;
}

function PromptTemplateList({ onSelect }: { onSelect: (id: string) => void }) {
  const { t } = useTranslation("dashboard");
  const [state, setState] = useState<Load<PromptTemplateMeta[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    API.listPromptTemplates({ signal: controller.signal }).then(
      (response) => {
        if (!controller.signal.aborted) setState({ status: "ready", data: response.templates });
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setState({ status: "error", message: errMsg(err) });
      },
    );
    return () => controller.abort();
  }, [attempt]);

  const groups = useMemo(() => {
    const byCategory = new Map<string, PromptTemplateMeta[]>();
    if (state.status !== "ready") return [];
    for (const template of state.data) {
      const items = byCategory.get(template.category) ?? [];
      items.push(template);
      byCategory.set(template.category, items);
    }
    return [...byCategory];
  }, [state]);

  const retry = () => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  };

  return (
    <section className="space-y-6">
      <header>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          Prompt Templates
        </div>
        <h2 className="font-editorial mt-1 text-[24px] leading-tight text-text">
          {t("prompt_templates")}
        </h2>
        <p className="mt-1.5 max-w-[62ch] text-[12.5px] leading-[1.6] text-text-3">
          {t("prompt_templates_desc")}
        </p>
      </header>

      {state.status === "loading" && <LoadingCard label={t("prompt_templates_loading")} />}
      {state.status === "error" && (
        <ErrorCard
          title={t("prompt_templates_load_failed")}
          message={state.message}
          onRetry={retry}
        />
      )}
      {state.status === "ready" && groups.length === 0 && (
        <div
          className="rounded-[10px] border border-hairline px-5 py-6 text-[12.5px] text-text-3"
          style={CARD_STYLE}
        >
          {t("prompt_templates_empty")}
        </div>
      )}
      {groups.map(([category, items]) => (
        <section key={category} aria-labelledby={`prompt-template-category-${category}`}>
          <div className="mb-2.5 flex items-baseline justify-between gap-3">
            <h3
              id={`prompt-template-category-${category}`}
              className="text-[14.5px] font-medium text-text"
            >
              {categoryLabel(t, category)}
            </h3>
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-4">
              {t("prompt_templates_count", { count: items.length })}
            </span>
          </div>
          <ul
            className="divide-y divide-hairline-soft overflow-hidden rounded-[10px] border border-hairline"
            style={CARD_STYLE}
          >
            {items.map((template) => (
              <li key={template.id}>
                <button
                  type="button"
                  onClick={() => onSelect(template.id)}
                  className="group flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-bg-grad-a/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] font-medium text-text">
                      {template.title}
                    </span>
                    <span className="mt-0.5 block text-[12px] leading-[1.55] text-text-3">
                      {template.description}
                    </span>
                  </span>
                  <span className="hidden shrink-0 font-mono text-[10.5px] text-text-4 sm:block">
                    {template.id}
                  </span>
                  <ChevronRight
                    aria-hidden
                    className="h-3.5 w-3.5 shrink-0 text-text-4 transition-colors group-hover:text-text-2"
                  />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </section>
  );
}
