import { RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { CallType, UsageSummary } from "@/types";
import type { UsageRecordsFilters, UsageTimeRange } from "@/stores/usage-records-store";
import { MEDIA_META, providerLabelResolver } from "./usage-record-format";

interface UsageFilterBarProps {
  filters: UsageRecordsFilters;
  summary: UsageSummary | null;
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

// 端点试跑记录的项目名是空串，「全部项目」不能用空串表示，另取一个哨兵值。同名项目
// 只会失去在这个下拉里被单独选中的能力，不影响其记录的展示。
const ALL_PROJECTS = "__all__";

const SELECT_CLS =
  "focus-ring h-[30px] rounded-[7px] border border-hairline-soft bg-bg-grad-a/45 px-2 text-[11.5px] text-text-2 transition-colors hover:border-hairline";

export function UsageFilterBar({
  filters,
  summary,
  onChange,
  onRefresh,
  refreshing,
}: UsageFilterBarProps) {
  const { t } = useTranslation("dashboard");
  const providerLabel = providerLabelResolver(summary);
  const options = summary?.filter_options;
  // 选定供应商后只列它的模型：跨供应商的同名模型混在一起既选不准也读不懂。
  const models = (options?.models ?? []).filter(
    (option) => !filters.provider || option.provider === filters.provider,
  );

  const projectLabel = (name: string) => name || t("usage_project_untitled");

  const chips: { key: string; label: string; clear: Partial<UsageRecordsFilters> }[] = [];
  if (filters.project !== null) {
    chips.push({
      key: "project",
      label: projectLabel(filters.project),
      clear: { project: null },
    });
  }
  if (filters.provider) {
    chips.push({
      key: "provider",
      label: providerLabel(filters.provider),
      clear: { provider: null, model: null },
    });
  }
  if (filters.model) {
    chips.push({ key: "model", label: filters.model, clear: { model: null } });
  }
  if (filters.mediaType) {
    chips.push({
      key: "media",
      label: t(MEDIA_META[filters.mediaType].labelKey),
      clear: { mediaType: null },
    });
  }
  // 分镜没有下拉可选，只由「需要关注」的连续失败条目写入；chip 是它唯一的出口。
  if (filters.segment) {
    chips.push({
      key: "segment",
      label: t("usage_target_segment", { id: filters.segment }),
      clear: { segment: null },
    });
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div
        role="group"
        aria-label={t("usage_range_label")}
        className="flex items-center gap-1"
      >
        {RANGES.map((range) => {
          const active = filters.range === range.value;
          return (
            <button
              key={range.value}
              type="button"
              aria-pressed={active}
              onClick={() => onChange({ range: range.value })}
              className={
                "focus-ring rounded-[7px] border px-2.5 py-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors " +
                (active
                  ? "border-accent/45 bg-accent-dim text-accent-2"
                  : "border-hairline-soft bg-bg-grad-a/45 text-text-3 hover:border-hairline hover:text-text")
              }
            >
              {t(range.labelKey)}
            </button>
          );
        })}
      </div>

      <select
        aria-label={t("usage_filter_project")}
        className={SELECT_CLS}
        value={filters.project ?? ALL_PROJECTS}
        onChange={(e) =>
          onChange({ project: e.target.value === ALL_PROJECTS ? null : e.target.value })
        }
      >
        <option value={ALL_PROJECTS}>{t("usage_filter_all_projects")}</option>
        {(options?.projects ?? []).map((name) => (
          <option key={name} value={name}>
            {projectLabel(name)}
          </option>
        ))}
      </select>

      <select
        aria-label={t("usage_filter_provider")}
        className={SELECT_CLS}
        value={filters.provider ?? ""}
        onChange={(e) =>
          onChange({ provider: e.target.value || null, model: null })
        }
      >
        <option value="">{t("usage_filter_all_providers")}</option>
        {(options?.providers ?? []).map((option) => (
          <option key={option.provider} value={option.provider}>
            {option.label}
          </option>
        ))}
      </select>

      <select
        aria-label={t("usage_filter_model")}
        className={SELECT_CLS}
        value={filters.model ?? ""}
        onChange={(e) => onChange({ model: e.target.value || null })}
      >
        <option value="">{t("usage_filter_all_models")}</option>
        {models.map((option) => (
          <option key={`${option.provider}/${option.model}`} value={option.model}>
            {option.model}
          </option>
        ))}
      </select>

      <select
        aria-label={t("usage_filter_media_type")}
        className={SELECT_CLS}
        value={filters.mediaType ?? ""}
        onChange={(e) =>
          onChange({ mediaType: (e.target.value || null) as CallType | null })
        }
      >
        <option value="">{t("usage_filter_all_media_types")}</option>
        {MEDIA_TYPES.map((type) => (
          <option key={type} value={type}>
            {t(MEDIA_META[type].labelKey)}
          </option>
        ))}
      </select>

      <button
        type="button"
        onClick={onRefresh}
        className="focus-ring ml-auto inline-flex items-center gap-1.5 rounded-[7px] px-2 py-1.5 text-[11.5px] text-text-3 transition-colors hover:text-text"
      >
        <RefreshCw
          aria-hidden="true"
          className={"h-3.5 w-3.5" + (refreshing ? " animate-spin" : "")}
        />
        {t("usage_refresh")}
      </button>

      {chips.length > 0 && (
        <div className="flex w-full flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip.key}
              className="inline-flex items-center gap-1 rounded-full border border-hairline-soft px-2 py-0.5 text-[11px] text-text-2"
            >
              {chip.label}
              <button
                type="button"
                aria-label={t("usage_filter_chip_clear", { label: chip.label })}
                onClick={() => onChange(chip.clear)}
                className="focus-ring rounded-full text-text-4 transition-colors hover:text-text"
              >
                <X aria-hidden="true" className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
