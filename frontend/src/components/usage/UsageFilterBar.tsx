import { RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CallType, EpisodeItemRef, UsageSummary } from "@/types";
import type { UsageRecordsFilters, UsageTimeRange } from "@/stores/usage-records-store";
import { episodeItemRefLabel } from "@/utils/episode-display";
import { MEDIA_META, projectTitleResolver, providerLabelResolver, usageProjectLabel } from "./usage-record-format";

interface UsageFilterBarProps {
  filters: UsageRecordsFilters;
  summary: UsageSummary | null;
  /** 分镜筛选值所属集的标题与播出位置，取自已载入的记录；没有时显示未命名集与集内 ID。 */
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

// 下拉的「全部」需要一个值：端点试跑记录的项目名是空串，不能用空串表示全部，另取一个哨兵值。
// 同名项目只会失去在这个下拉里被单独选中的能力，不影响其记录的展示。
const ALL = "__all__";

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
  const items = [{ value: ALL, label: allLabel }, ...options];
  return (
    <Select items={items} value={value ?? ALL} onValueChange={(next) => onChange(next === ALL ? null : next)}>
      <SelectTrigger size="sm" aria-label={label} className="w-40">
        <SelectValue />
      </SelectTrigger>
      {/* 项目标题、供应商名可能很长：下拉可比触发器宽，超过上限的选项截断，悬停看全文 */}
      <SelectContent alignItemWithTrigger={false} align="start" className="w-auto max-w-md min-w-(--anchor-width)">
        {items.map((item) => (
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
  // 选定供应商后只列它的模型：跨供应商的同名模型混在一起既选不准也读不懂。
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
  // 分镜没有下拉可选，只由「需要关注」的连续失败条目写入；chip 是它唯一的出口。
  if (filters.segment) {
    chips.push({
      key: "segment",
      label: t("usage_target_segment", { id: episodeItemRefLabel(filters.segment, segmentRef, t) }),
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
          // 同名模型在不同供应商下各出现一次；下拉只按模型名筛选，去重后列出。
          options={[...new Set(models.map((option) => option.model))].map((model) => ({ value: model, label: model }))}
          onChange={(model) => onChange({ model })}
        />
        <FilterSelect
          label={t("usage_filter_media_type")}
          value={filters.mediaType}
          allLabel={t("usage_filter_all_media_types")}
          options={MEDIA_TYPES.map((type) => ({ value: type, label: t(MEDIA_META[type].labelKey) }))}
          onChange={(value) => onChange({ mediaType: value as CallType | null })}
        />

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
