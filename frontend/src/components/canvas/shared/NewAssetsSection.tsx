import { useId } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { NewAssetDecision, NewAssetType, PlanNewAsset, ProjectData } from "@/types";
import { useProjectsStore } from "@/stores/projects-store";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { normalizeAssetName } from "@/utils/reference-mentions";
import { itemIdWithinEpisode } from "@/utils/episode-display";

/** 一个规划条目引用到的名字与它的原文片段，供新增项展开出场位置。 */
export interface NewAssetEntryRefs {
  id: string;
  /** 条目里引用到的全部名字（引用数组、说话人、正文 `@[名]`），未归一。 */
  names: string[];
  snippet: string;
}

interface NewAssetsSectionProps {
  items: PlanNewAsset[];
  entries: NewAssetEntryRefs[];
  /** 已确认或由 Agent 编辑中：只展示处理，不可改。 */
  readOnly: boolean;
  disabled: boolean;
  onChange: (items: PlanNewAsset[]) => void;
}

/**
 * 草稿里的 `new_assets` 可能被 Agent 改坏：缺键时视为没有新增项，形状或取值不对时返回 false，由调用方
 * 退回只读呈现。
 */
export function hasValidNewAssets(content: Record<string, unknown> | null): boolean {
  const items = content?.new_assets;
  if (items === undefined) return true;
  return (
    Array.isArray(items) &&
    items.every((item: unknown) => {
      if (item == null || typeof item !== "object") return false;
      const v = item as Record<string, unknown>;
      return (
        typeof v.name === "string" &&
        typeof v.type === "string" &&
        Object.hasOwn(BUCKET, v.type) &&
        typeof v.decision === "string" &&
        (DECISIONS as readonly string[]).includes(v.decision) &&
        typeof v.reason === "string" &&
        typeof v.description === "string" &&
        typeof v.target === "string" &&
        typeof v.asset_name === "string" &&
        Array.isArray(v.aliases)
      );
    })
  );
}

const DECISIONS: readonly NewAssetDecision[] = ["register", "merge", "derivative", "skip"];

const BUCKET: Record<NewAssetType, "characters" | "scenes" | "props"> = {
  character: "characters",
  scene: "scenes",
  prop: "props",
};

type Group = "character" | "derivative" | "scene" | "prop";

const GROUPS: readonly Group[] = ["character", "derivative", "scene", "prop"];

const GROUP_LABEL_KEY: Record<Group, string> = {
  character: "segment_refs_badge_character",
  derivative: "segment_refs_badge_character_derivative",
  scene: "segment_refs_badge_scene",
  prop: "segment_refs_badge_prop",
};

/** 行左侧细边按处理方式着色：一眼分出会新建资产、归并、衍生与不登记的项。 */
const DECISION_EDGE: Record<NewAssetDecision, string> = {
  register: "border-l-primary",
  merge: "border-l-muted-foreground",
  derivative: "border-l-primary",
  skip: "border-l-border",
};

function groupOf(item: PlanNewAsset): Group {
  return item.type === "character" && item.decision === "derivative" ? "derivative" : item.type;
}

function registeredNames(project: ProjectData | null, type: NewAssetType): string[] {
  return Object.keys(project?.[BUCKET[type]] ?? {});
}

/** 确认时自动归到的已登记同类资产：称呼与它同名（规范化后）的任意项，或登记名与它同名的「登记为新资产」项。 */
function autoMergeTarget(item: PlanNewAsset, registered: string[]): string | null {
  const find = (value: string) => {
    const key = normalizeAssetName(value);
    return registered.find((name) => normalizeAssetName(name) === key) ?? null;
  };
  const byName = find(item.name);
  if (byName != null || item.decision !== "register") return byName;
  return item.asset_name ? find(item.asset_name) : null;
}

function summaryText(t: TFunction, item: PlanNewAsset, autoTarget: string | null): string {
  if (autoTarget != null) return t("new_asset_summary_auto_merged", { target: autoTarget });
  switch (item.decision) {
    case "register":
      return t("new_asset_summary_register", { name: item.asset_name || item.name });
    case "merge":
      return item.target
        ? t("new_asset_summary_merge", { target: item.target, name: item.name })
        : t("new_asset_summary_target_missing");
    case "derivative":
      return item.target
        ? t("new_asset_summary_derivative", { target: item.target, derivative: item.asset_name || item.name })
        : t("new_asset_summary_base_missing");
    case "skip":
      return t("new_asset_summary_skip");
  }
}

function TargetSelect({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const choices = value && !options.includes(value) ? [value, ...options] : options;
  const items = [
    { value: "", label: t("new_asset_target_placeholder") },
    ...choices.map((name) => ({ value: name, label: name })),
  ];
  return (
    <div className="flex flex-col items-start gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <Select
        items={items}
        value={value}
        onValueChange={(next) => {
          if (next !== null) onChange(next);
        }}
        disabled={disabled}
      >
        <SelectTrigger size="sm" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false} align="start">
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function TextField({
  label,
  value,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  placeholder?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1 text-xs text-muted-foreground">
      <label htmlFor={id}>{label}</label>
      <Input id={id} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} disabled={disabled} />
    </div>
  );
}

function DescriptionField({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <Textarea value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} aria-label={label} />
    </div>
  );
}

function NewAssetRow({
  item,
  items,
  index,
  project,
  entries,
  readOnly,
  disabled,
  onPatch,
}: {
  item: PlanNewAsset;
  items: PlanNewAsset[];
  index: number;
  project: ProjectData | null;
  entries: NewAssetEntryRefs[];
  readOnly: boolean;
  disabled: boolean;
  onPatch: (patch: Partial<PlanNewAsset>) => void;
}) {
  const { t } = useTranslation("dashboard");
  const registered = registeredNames(project, item.type);
  const autoTarget = autoMergeTarget(item, registered);
  const key = normalizeAssetName(item.name);
  const appearances = entries.filter((entry) => entry.names.some((name) => normalizeAssetName(name) === key));
  // 归到已有资产：同类已登记资产，加上本集其他登记为新资产的同类新增项。
  const otherNewNames = items
    .filter((other, i) => i !== index && other.type === item.type && other.decision === "register")
    .map((other) => other.asset_name || other.name);
  const mergeOptions = [...new Set([...registered, ...otherNewNames])];
  const baseOptions = [
    ...new Set([
      ...registeredNames(project, "character"),
      ...items
        .filter((other, i) => i !== index && other.type === "character" && other.decision === "register")
        .map((other) => other.asset_name || other.name),
    ]),
  ];
  const decisions = item.type === "character" ? DECISIONS : DECISIONS.filter((d) => d !== "derivative");
  const decisionItems = decisions.map((decision) => ({ value: decision, label: t(`new_asset_decision_${decision}`) }));

  return (
    <li
      className={`flex flex-col gap-2 rounded-md border border-l-2 border-border/50 px-3 py-2.5 ${
        DECISION_EDGE[autoTarget != null ? "merge" : item.decision]
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">{item.name}</span>
        <span className="flex-1 text-xs text-muted-foreground">{summaryText(t, item, autoTarget)}</span>
        {!readOnly && (
          <Select
            items={decisionItems}
            value={item.decision}
            onValueChange={(next) => {
              if (next !== null) onPatch({ decision: next });
            }}
            disabled={disabled}
          >
            <SelectTrigger size="sm" aria-label={t("new_asset_decision_label", { name: item.name })}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false} align="end">
              {decisionItems.map((decision) => (
                <SelectItem key={decision.value} value={decision.value}>
                  {decision.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      {item.reason && <p className="text-xs text-muted-foreground">{t("new_asset_reason", { reason: item.reason })}</p>}

      {!readOnly && item.decision === "register" && autoTarget == null && (
        <div className="grid gap-2">
          <TextField
            label={t("new_asset_field_asset_name")}
            value={item.asset_name}
            placeholder={item.name}
            disabled={disabled}
            onChange={(asset_name) => onPatch({ asset_name })}
          />
          <DescriptionField
            label={t("new_asset_field_description")}
            value={item.description}
            disabled={disabled}
            onChange={(description) => onPatch({ description })}
          />
        </div>
      )}
      {!readOnly && item.decision === "merge" && autoTarget == null && (
        <TargetSelect
          label={t("new_asset_field_merge_target")}
          value={item.target}
          options={mergeOptions}
          disabled={disabled}
          onChange={(target) => onPatch({ target })}
        />
      )}
      {!readOnly && item.decision === "derivative" && autoTarget == null && (
        <div className="grid gap-2">
          <TargetSelect
            label={t("new_asset_field_base")}
            value={item.target}
            options={baseOptions}
            disabled={disabled}
            onChange={(target) => onPatch({ target })}
          />
          <TextField
            label={t("new_asset_field_derivative_name")}
            value={item.asset_name}
            placeholder={item.name}
            disabled={disabled}
            onChange={(asset_name) => onPatch({ asset_name })}
          />
          <DescriptionField
            label={t("new_asset_field_change")}
            value={item.description}
            disabled={disabled}
            onChange={(description) => onPatch({ description })}
          />
        </div>
      )}

      {appearances.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2" />}>
            <ChevronDown aria-hidden data-icon="inline-start" className="transition-transform group-aria-expanded/button:rotate-180" />
            {t("new_asset_appearances", { count: appearances.length })}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="mt-1.5 flex flex-col gap-1">
              {appearances.map((entry) => (
                <li key={entry.id} className="flex items-start gap-2 text-xs leading-relaxed">
                  <span className="shrink-0 rounded-sm bg-muted px-1.5 py-px text-muted-foreground tabular-nums">
                    {itemIdWithinEpisode(entry.id)}
                  </span>
                  <span className="text-muted-foreground">{entry.snippet}</span>
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </li>
  );
}

/**
 * 内容确认页的「本集新增资产」区：按角色、衍生、场景、道具分组列出 AI 规划带出的新增项与处理，
 * 默认照 AI 的决定确认，可逐项改动。确认时由后端按这份处理登记资产并改写引用。
 */
export function NewAssetsSection({ items, entries, readOnly, disabled, onChange }: NewAssetsSectionProps) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const project = useProjectsStore((s) => s.currentProjectData);
  if (items.length === 0) return null;

  const autoMergedCount = items.filter(
    (item) => autoMergeTarget(item, registeredNames(project, item.type)) != null,
  ).length;
  const patchItem = (index: number, patch: Partial<PlanNewAsset>) =>
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h3 id={titleId} className="text-sm font-medium text-foreground">
          {t("new_assets_title")}
        </h3>
        <p className="text-xs text-muted-foreground">
          {t(readOnly ? "new_assets_readonly_description" : "new_assets_description")}
        </p>
      </div>
      <div className="rounded-lg border border-border bg-card p-4">
        {autoMergedCount > 0 && (
          <p className="mb-3 text-xs text-muted-foreground">{t("new_assets_auto_merged_notice", { count: autoMergedCount })}</p>
        )}
        <div className="flex flex-col gap-4">
          {GROUPS.map((group) => {
            const indexed = items.map((item, index) => ({ item, index })).filter(({ item }) => groupOf(item) === group);
            if (indexed.length === 0) return null;
            return (
              <section key={group} aria-label={t(GROUP_LABEL_KEY[group])} className="flex flex-col gap-2">
                <h4 className="text-xs font-medium text-muted-foreground">{t(GROUP_LABEL_KEY[group])}</h4>
                <ul className="flex flex-col gap-2">
                  {indexed.map(({ item, index }) => (
                    <NewAssetRow
                      key={`${item.type}-${item.name}-${String(index)}`}
                      item={item}
                      items={items}
                      index={index}
                      project={project}
                      entries={entries}
                      readOnly={readOnly}
                      disabled={disabled}
                      onPatch={(patch) => patchItem(index, patch)}
                    />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </div>
    </section>
  );
}
