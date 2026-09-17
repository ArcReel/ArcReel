import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { SectionShell } from "@/components/ui/SectionShell";
import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type { PromptTemplateDetail, PromptTemplateMeta } from "@/types";
import { errMsg } from "@/utils/async";
import { flattenOutputSchema } from "./promptTemplateSchema";

type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

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

function PromptTemplateDetailView({
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
                <dt className="font-mono text-[12px] text-accent-2">{`{{ ${name} }}`}</dt>
                <dd className="text-[12.5px] leading-[1.55] text-text-2">{description}</dd>
              </div>
            ))}
          </dl>
        )}
      </SectionShell>

      <SectionShell
        kicker="Source"
        title={t("prompt_templates_source")}
        description={t("prompt_templates_source_desc")}
      >
        <MarkedSource text={source} />
      </SectionShell>

      <SectionShell
        kicker="Partials"
        title={t("prompt_templates_partials")}
        description={t("prompt_templates_partials_desc")}
      >
        {partials.length === 0 ? (
          <p className="text-[12.5px] text-text-3">{t("prompt_templates_partials_empty")}</p>
        ) : (
          <ul className="space-y-3">
            {partials.map((partial) => (
              <li key={partial.name}>
                <div className="mb-1 font-mono text-[11px] text-text-2">{partial.name}</div>
                {partial.source.trim() === "" ? (
                  <p className="text-[12px] italic text-text-4">
                    {t("prompt_templates_partial_blank")}
                  </p>
                ) : (
                  <MarkedSource text={partial.source} />
                )}
              </li>
            ))}
          </ul>
        )}
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

const MARKER_PATTERN = /(\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/;

/** 标记按语义着色：数据槽位、片段引用、条件与循环块各一种。 */
function markerClass(marker: string): string {
  if (marker.startsWith("{%")) return "bg-bg-grad-a/70 text-text-4";
  if (/\b(partial|variant)\(/.test(marker)) return "bg-warm-tint text-warm-bright";
  return "bg-accent-dim text-accent-2";
}

/** 模版源文原样展示，模版表达式以标记突出。 */
function MarkedSource({ text }: { text: string }) {
  const parts = text.split(MARKER_PATTERN);
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-[8px] border border-hairline-soft bg-bg-grad-b/60 px-3.5 py-3 font-mono text-[12px] leading-[1.7] text-text-2">
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <mark key={index} className={`rounded-[4px] px-0.5 ${markerClass(part)}`}>
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </pre>
  );
}

function LoadingCard({ label }: { label: string }) {
  return (
    <div
      className="flex items-center gap-2 rounded-[10px] border border-hairline px-5 py-6 text-text-3"
      style={CARD_STYLE}
    >
      <Loader2 aria-hidden className="h-3.5 w-3.5 text-accent-2 motion-safe:animate-spin" />
      <span className="text-[12.5px]">{label}</span>
    </div>
  );
}

function ErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  const { t } = useTranslation("common");
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-[10px] border px-4 py-3.5"
      style={{ borderColor: "var(--color-warm-ring)", background: "var(--color-warm-tint)" }}
    >
      <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warm-bright" />
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] font-medium text-text">{title}</p>
        {message && <p className="mt-0.5 text-[12px] text-text-3">{message}</p>}
      </div>
      <button type="button" onClick={onRetry} className={GHOST_BTN_CLS}>
        <RefreshCcw aria-hidden className="h-3.5 w-3.5" />
        {t("retry")}
      </button>
    </div>
  );
}

function categoryLabel(t: (key: string, options: { defaultValue: string }) => string, category: string) {
  return t(`prompt_templates_category_${category}`, { defaultValue: category });
}
