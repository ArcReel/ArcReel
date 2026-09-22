import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { PromptTemplateMeta } from "@/types";
import { errMsg } from "@/utils/async";
import { categoryLabel, ErrorCard, LoadingCard, type Load } from "./promptTemplateShared";

/** 按流水线顺序排列；接口返回的其他类别按首次出现顺序排在最后。 */
const CATEGORY_ORDER = ["text", "asset", "storyboard", "video", "style"];

/** 默认折叠为紧凑表的类别。 */
const COMPACT_CATEGORIES = new Set(["style"]);

const FILTER_AXES = ["content_mode", "generation_mode", "source_kind"] as const;
type FilterAxis = (typeof FILTER_AXES)[number];
type AxisFilter = Partial<Record<FilterAxis, string>>;

/** 轴值沿用新建项目与项目设置里的用户文案；未收录的取值原样显示。 */
const AXIS_VALUE_LABEL_KEYS: Record<FilterAxis, Record<string, string>> = {
  content_mode: {
    narration: "narration_visuals",
    drama: "drama_animation",
    ad: "ad_short_video",
  },
  generation_mode: {
    storyboard: "route_storyboard",
    reference_video: "route_reference_video",
  },
  source_kind: {
    novel: "source_kind_novel",
    screenplay: "source_kind_screenplay",
  },
};

function groupByCategory(templates: PromptTemplateMeta[]): [string, PromptTemplateMeta[]][] {
  const byCategory = new Map<string, PromptTemplateMeta[]>();
  for (const template of templates) {
    const items = byCategory.get(template.category) ?? [];
    items.push(template);
    byCategory.set(template.category, items);
  }
  const rank = (category: string) => {
    const index = CATEGORY_ORDER.indexOf(category);
    return index === -1 ? CATEGORY_ORDER.length : index;
  };
  return [...byCategory].sort(([a], [b]) => rank(a) - rank(b));
}

/** 各筛选轴在全部模版 `applies_to` 中出现过的取值，按首次出现顺序。 */
function collectAxisValues(templates: PromptTemplateMeta[]): [FilterAxis, string[]][] {
  return FILTER_AXES.map((axis): [FilterAxis, string[]] => {
    const values = new Set<string>();
    for (const template of templates) {
      for (const value of template.applies_to[axis] ?? []) values.add(value);
    }
    return [axis, [...values]];
  }).filter(([, values]) => values.length > 0);
}

/** 未声明某轴的模版对该轴全部取值适用，任何筛选下都保留。 */
function matchesFilter(template: PromptTemplateMeta, filter: AxisFilter): boolean {
  return FILTER_AXES.every((axis) => {
    const selected = filter[axis];
    const values = template.applies_to[axis];
    return selected === undefined || !values?.length || values.includes(selected);
  });
}

/** 模版列表：按流水线顺序分组，画风组默认折叠，可按创作类型、生成模式、源文件类型筛选。 */
export function PromptTemplateList({ onSelect }: { onSelect: (id: string) => void }) {
  const { t } = useTranslation("dashboard");
  const [state, setState] = useState<Load<PromptTemplateMeta[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState<AxisFilter>({});

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

  const templates = useMemo(() => (state.status === "ready" ? state.data : []), [state]);
  const axisValues = useMemo(() => collectAxisValues(templates), [templates]);
  const groups = useMemo(
    () => groupByCategory(templates.filter((template) => matchesFilter(template, filter))),
    [templates, filter],
  );

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
      {state.status === "ready" && templates.length === 0 && (
        <EmptyCard>{t("prompt_templates_empty")}</EmptyCard>
      )}
      {axisValues.length > 0 && (
        <AxisFilterBar
          axisValues={axisValues}
          filter={filter}
          onChange={(axis, value) => setFilter((prev) => ({ ...prev, [axis]: value }))}
        />
      )}
      {templates.length > 0 && groups.length === 0 && (
        <EmptyCard>{t("prompt_templates_filter_empty")}</EmptyCard>
      )}
      {groups.map(([category, items]) => (
        <CategoryGroup
          key={category}
          category={category}
          items={items}
          compact={COMPACT_CATEGORIES.has(category)}
          onSelect={onSelect}
        />
      ))}
    </section>
  );
}

function AxisFilterBar({
  axisValues,
  filter,
  onChange,
}: {
  axisValues: [FilterAxis, string[]][];
  filter: AxisFilter;
  onChange: (axis: FilterAxis, value: string | undefined) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      className="space-y-2 rounded-[10px] border border-hairline px-4 py-3"
      style={CARD_STYLE}
    >
      {axisValues.map(([axis, values]) => {
        const axisLabel = t(`prompt_templates_axis_${axis}`);
        const options: [string | undefined, string][] = [
          [undefined, t("prompt_templates_filter_all")],
          ...values.map((value): [string, string] => {
            const key = AXIS_VALUE_LABEL_KEYS[axis][value];
            return [value, key ? t(key) : value];
          }),
        ];
        return (
          <div
            key={axis}
            role="group"
            aria-label={axisLabel}
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5"
          >
            <span className="w-24 shrink-0 text-[12px] text-text-3">{axisLabel}</span>
            <div className="flex flex-wrap items-center gap-1">
              {options.map(([value, label]) => {
                const active = filter[axis] === value;
                return (
                  <button
                    key={value ?? ""}
                    type="button"
                    aria-pressed={active}
                    title={value}
                    onClick={() => onChange(axis, value)}
                    className={
                      "focus-ring rounded-full px-2.5 py-0.5 text-[12px] transition-colors " +
                      (active ? "bg-accent-dim text-accent-2" : "text-text-3 hover:text-text")
                    }
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CategoryGroup({
  category,
  items,
  compact,
  onSelect,
}: {
  category: string;
  items: PromptTemplateMeta[];
  compact: boolean;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [expanded, setExpanded] = useState(!compact);
  const headingId = `prompt-template-category-${category}`;
  const listId = `prompt-template-list-${category}`;
  const label = categoryLabel(t, category);

  return (
    <section aria-labelledby={headingId}>
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h3 id={headingId} className="text-[14.5px] font-medium text-text">
          {compact ? (
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={listId}
              onClick={() => setExpanded((open) => !open)}
              className="group inline-flex items-center gap-1.5 rounded-[5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <ChevronRight
                aria-hidden
                className={`h-3.5 w-3.5 text-text-4 motion-safe:transition-transform group-hover:text-text-2 ${expanded ? "rotate-90" : ""}`}
              />
              {label}
            </button>
          ) : (
            label
          )}
        </h3>
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-4">
          {t("prompt_templates_count", { count: items.length })}
        </span>
      </div>
      {compact ? (
        expanded && (
          <ul
            id={listId}
            className="grid grid-cols-2 overflow-hidden rounded-[10px] border border-hairline py-1"
            style={CARD_STYLE}
          >
            {items.map((template) => (
              <li key={template.id}>
                <button
                  type="button"
                  onClick={() => onSelect(template.id)}
                  title={template.id}
                  className="group flex w-full items-center gap-2 px-3.5 py-2 text-left transition-colors hover:bg-bg-grad-a/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
                >
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-text-2 group-hover:text-text">
                    {template.title}
                  </span>
                  <ChevronRight
                    aria-hidden
                    className="h-3 w-3 shrink-0 text-text-4 transition-colors group-hover:text-text-2"
                  />
                </button>
              </li>
            ))}
          </ul>
        )
      ) : (
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
                  <span className="block text-[13px] font-medium text-text">{template.title}</span>
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
      )}
    </section>
  );
}

function EmptyCard({ children }: { children: ReactNode }) {
  return (
    <div
      className="rounded-[10px] border border-hairline px-5 py-6 text-[12.5px] text-text-3"
      style={CARD_STYLE}
    >
      {children}
    </div>
  );
}
