import { useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { PromptTemplateMeta, PromptTemplateTrigger } from "@/types";
import { errMsg } from "@/utils/async";
import { categoryLabel } from "./promptTemplateCategory";
import { EmptyCard, ErrorCard, LoadingCard, type Load } from "./promptTemplateShared";

/** Pipeline ordering, appending other API-returned categories in first-seen order. */
const CATEGORY_ORDER = ["text", "asset", "storyboard", "video", "style"];

/** Categories collapsed to compact tables by default. */
const COMPACT_CATEGORIES = new Set(["style"]);

const FILTER_AXES = ["content_mode", "generation_mode", "source_kind"] as const;
type FilterAxis = (typeof FILTER_AXES)[number];
type AxisFilter = Partial<Record<FilterAxis, string>>;

/** Axis labels reuse creation/project-setting copy; unlisted values display unchanged. */
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

/**
 * Trigger-specific names reuse Agent-session tool names and task-queue task-type copy;
 * user actions/asset-image tasks lack existing equivalents and map separately. Unlisted values display unchanged.
 */
const TRIGGER_NAME_KEYS: Record<PromptTemplateTrigger["kind"], (name: string) => string> = {
  agent_tool: (name) => `tool_name_${name}`,
  user_action: (name) => `prompt_templates_trigger_${name}`,
  generation_task: (name) => (name === "asset" ? "prompt_templates_category_asset" : `task_type_${name}`),
};

type Translate = (key: string, options: { defaultValue: string }) => string;

function stageLabel(t: Translate, stage: string): string {
  return t(`prompt_templates_stage_${stage}`, { defaultValue: stage });
}

/** Kind · name, for example Agent tool · Generate script. */
function triggerLabel(t: Translate, trigger: PromptTemplateTrigger): string {
  const kind = t(`prompt_templates_trigger_kind_${trigger.kind}`, { defaultValue: trigger.kind });
  const name = t(TRIGGER_NAME_KEYS[trigger.kind](trigger.name), { defaultValue: trigger.name });
  return `${kind} · ${name}`;
}

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

/** Axis values found across all template `applies_to`, in first-seen order. */
function collectAxisValues(templates: PromptTemplateMeta[]): [FilterAxis, string[]][] {
  return FILTER_AXES.map((axis): [FilterAxis, string[]] => {
    const values = new Set<string>();
    for (const template of templates) {
      for (const value of template.applies_to[axis] ?? []) values.add(value);
    }
    return [axis, [...values]];
  }).filter(([, values]) => values.length > 0);
}

/** Templates omitting axes apply to all their values and remain under every filter. */
function matchesFilter(template: PromptTemplateMeta, filter: AxisFilter): boolean {
  return FILTER_AXES.every((axis) => {
    const selected = filter[axis];
    const values = template.applies_to[axis];
    return selected === undefined || !values?.length || values.includes(selected);
  });
}

/** Pipeline-grouped templates; styles collapse by default; filter creation type, generation mode and source type. */
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
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-medium">{t("prompt_templates")}</h2>
        <p className="max-w-prose text-sm text-muted-foreground">{t("prompt_templates_desc")}</p>
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
    </div>
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
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-card px-4 py-3">
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
          <div key={axis} role="group" aria-label={axisLabel} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="w-24 shrink-0 text-xs text-muted-foreground">{axisLabel}</span>
            <div className="flex flex-wrap items-center gap-1">
              {options.map(([value, label]) => {
                const active = filter[axis] === value;
                return (
                  <Button
                    key={value ?? ""}
                    variant={active ? "secondary" : "ghost"}
                    size="xs"
                    aria-pressed={active}
                    title={value}
                    onClick={() => onChange(axis, value)}
                  >
                    {label}
                  </Button>
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
  const headingId = `prompt-template-category-${category}`;
  const label = categoryLabel(t, category);
  const count = <span className="text-xs text-muted-foreground">{t("prompt_templates_count", { count: items.length })}</span>;

  // Long-tail categories such as styles collapse into compact two-column tables; others show explanatory rows.
  if (compact) {
    return (
      <Collapsible render={<section aria-labelledby={headingId} />}>
        <div className="flex items-baseline justify-between gap-3">
          <h3 id={headingId} className="text-sm font-medium">
            <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2.5" />}>
              <ChevronRight
                aria-hidden
                className="text-muted-foreground transition-transform group-aria-expanded/button:rotate-90"
              />
              {label}
            </CollapsibleTrigger>
          </h3>
          {count}
        </div>
        <CollapsibleContent className="mt-2.5">
          <ul className="@container grid grid-cols-1 overflow-hidden rounded-lg border border-border bg-card py-1 @md:grid-cols-2">
            {items.map((template) => (
              <li key={template.id} className="min-w-0">
                <button
                  type="button"
                  onClick={() => onSelect(template.id)}
                  title={template.id}
                  className={`${ROW_CLS} gap-2 px-3.5 py-2`}
                >
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-sm text-subtle-foreground group-hover:text-foreground">
                      {template.title}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {stageLabel(t, template.stage)} · {triggerLabel(t, template.invoked_by)}
                    </span>
                  </span>
                  <ChevronRight aria-hidden className={CHEVRON_CLS} />
                </button>
              </li>
            ))}
          </ul>
        </CollapsibleContent>
      </Collapsible>
    );
  }

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id={headingId} className="text-sm font-medium">
          {label}
        </h3>
        {count}
      </div>
      <ul className="@container divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
        {items.map((template) => (
          <li key={template.id}>
            <button type="button" onClick={() => onSelect(template.id)} className={`${ROW_CLS} gap-4 px-4 py-3`}>
              <span className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="text-sm font-medium">{template.title}</span>
                <span className="text-sm text-muted-foreground">{template.description}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
                  <Badge variant="secondary" title={template.stage}>
                    {stageLabel(t, template.stage)}
                  </Badge>
                  <Badge variant="outline" title={`${template.invoked_by.kind}:${template.invoked_by.name}`}>
                    {triggerLabel(t, template.invoked_by)}
                  </Badge>
                </span>
              </span>
              <span className="hidden shrink-0 font-mono text-xs text-muted-foreground @lg:block">{template.id}</span>
              <ChevronRight aria-hidden className={CHEVRON_CLS} />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

const ROW_CLS =
  "group flex w-full items-center text-left transition-colors hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset";
const CHEVRON_CLS = "size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-subtle-foreground";
