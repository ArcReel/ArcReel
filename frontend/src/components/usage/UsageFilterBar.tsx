import { RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CallType, EpisodeItemRef, UsageSummary } from "@/types";
import type { UsageRecordsFilters, UsageTimeRange } from "@/stores/usage-records-store";
import { episodeItemRefLabel } from "@/utils/episode-display";
import { episodeItemKind, isPositionedItemId } from "@/utils/episode-item";
import { MEDIA_META, projectTitleResolver, providerLabelResolver, usageProjectLabel } from "./usage-record-format";

interface UsageFilterBarProps {
  filters: UsageRecordsFilters;
  summary: UsageSummary | null;
  /** Storyboard-filter owning episode titles/broadcast positions from loaded records; absent values show Untitled episode/local IDs. */
  segmentRef?: EpisodeItemRef | null;
  onChange: (patch: Partial<UsageRecordsFilters>) => void;
  onRefresh: () => void;
  refreshing: boolean;
}

const RANGES: { value: UsageTimeRange; labelKey: string }[] = [
  { value: "7d", labelKey: "usage_range_7d" },
  { value: "30d", labelKey: "usage_range_30d" },
  { value: "90d", labelKey: "usage_range_90d" },
  { value: "all", labelKey: "all" },
];

const MEDIA_TYPES: CallType[] = ["image", "video", "text", "audio"];

interface FilterOption {
  value: string;
  label: string;
}

function FilterSelect({
  label,
  value,
  allLabel,
  options,
  onChange,
}: {
  label: string;
  value: string | null;
  allLabel: string;
  options: FilterOption[];
  onChange: (value: string | null) => void;
}) {
  // All uses null: project names can be empty (endpoint trials), and custom-model IDs are unrestricted, making all string sentinels collision-prone.
  const items: { value: string | null; label: string }[] = [{ value: null, label: allLabel }, ...options];
  return (
    <Select items={items} value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" aria-label={label} className="w-40">
        <SelectValue />
      </SelectTrigger>
      {/* Long project/provider names: dropdowns may exceed trigger widths; truncate over-limit options, revealing full text on hover. */}
      <SelectContent alignItemWithTrigger={false} align="start" className="w-auto max-w-md min-w-(--anchor-width)">
        <SelectItem value={null}>
          <TruncatedText text={allLabel} focusable={false} />
        </SelectItem>
        {options.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            <TruncatedText text={item.label} focusable={false} />
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function UsageFilterBar({
  filters,
  summary,
  segmentRef = null,
  onChange,
  onRefresh,
  refreshing,
}: UsageFilterBarProps) {
  const { t, i18n } = useTranslation("dashboard");
  const providerLabel = providerLabelResolver(summary);
  const titleOf = projectTitleResolver(summary);
  const options = summary?.filter_options;
  // Selected providers restrict model lists; cross-provider duplicate names are otherwise ambiguous/unreadable.
  const models = (options?.models ?? []).filter(
    (option) => !filters.provider || option.provider === filters.provider,
  );

  const projectLabel = (name: string) => usageProjectLabel(name, t, i18n.language, titleOf(name));

  const chips: { key: string; label: string; clear: Partial<UsageRecordsFilters> }[] = [];
  if (filters.project !== null) {
    chips.push({ key: "project", label: projectLabel(filters.project), clear: { project: null } });
  }
  if (filters.provider) {
    chips.push({ key: "provider", label: providerLabel(filters.provider), clear: { provider: null, model: null } });
  }
  if (filters.model) {
    chips.push({ key: "model", label: filters.model, clear: { model: null } });
  }
  if (filters.mediaType) {
    chips.push({ key: "media", label: t(MEDIA_META[filters.mediaType].labelKey), clear: { mediaType: null } });
  }
  // Storyboards lack selectable dropdowns, set only by Needs attention repeated-failure entries; chips provide their sole clearing exits.
  // Only storyboards/video units have type prefixes; grids/others use reference labels directly.
  if (filters.segment) {
    const id = episodeItemRefLabel(filters.segment, segmentRef, t);
    chips.push({
      key: "segment",
      label: isPositionedItemId(filters.segment)
        ? t(episodeItemKind(filters.segment) === "unit" ? "usage_target_unit" : "usage_target_segment", { id })
        : id,
      clear: { segment: null },
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label={t("usage_range_label")} className="flex items-center gap-0.5">
          {RANGES.map((range) => {
            const active = filters.range === range.value;
            return (
              <Button
                key={range.value}
                size="sm"
                variant={active ? "secondary" : "ghost"}
                aria-pressed={active}
                onClick={() => onChange({ range: range.value })}
              >
                {t(range.labelKey)}
              </Button>
            );
          })}
        </div>

        <FilterSelect
          label={t("usage_filter_project")}
          value={filters.project}
          allLabel={t("usage_filter_all_projects")}
          options={(options?.projects ?? []).map((name) => ({ value: name, label: projectLabel(name) }))}
          onChange={(project) => onChange({ project })}
        />
        <FilterSelect
          label={t("usage_filter_provider")}
          value={filters.provider}
          allLabel={t("usage_filter_all_providers")}
          options={(options?.providers ?? []).map((option) => ({ value: option.provider, label: option.label }))}
          onChange={(provider) => onChange({ provider, model: null })}
        />
        <FilterSelect
          label={t("usage_filter_model")}
          value={filters.model}
          allLabel={t("usage_filter_all_models")}
          // Same-named models appear per provider; dropdowns filter model names only, listing deduplicated values.
          options={[...new Set(models.map((option) => option.model))].map((model) => ({ value: model, label: model }))}
          onChange={(model) => onChange({ model })}
        />
        {/* All Agent records are text media, making filters useless; this dimension belongs only to Generation calls. */}
        {filters.view === "generation" && (
          <FilterSelect
            label={t("usage_filter_media_type")}
            value={filters.mediaType}
            allLabel={t("usage_filter_all_media_types")}
            options={MEDIA_TYPES.map((type) => ({ value: type, label: t(MEDIA_META[type].labelKey) }))}
            onChange={(value) => onChange({ mediaType: value as CallType | null })}
          />
        )}

        <Button size="sm" variant="ghost" className="ml-auto" onClick={onRefresh}>
          <RefreshCw aria-hidden="true" data-icon="inline-start" className={cn(refreshing && "animate-spin")} />
          {t("usage_refresh")}
        </Button>
      </div>

      {chips.length > 0 && (
        <ul aria-label={t("usage_filter_active")} className="flex flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <li
              key={chip.key}
              className="inline-flex max-w-64 items-center gap-0.5 rounded-full border border-border py-0.5 pr-0.5 pl-2.5 text-xs text-subtle-foreground"
            >
              <TruncatedText text={chip.label} />
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={t("usage_filter_chip_clear", { label: chip.label })}
                onClick={() => onChange(chip.clear)}
              >
                <X aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
