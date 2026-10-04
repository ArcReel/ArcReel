import { ChevronLeft, ChevronRight } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { UsageSummary } from "@/types";
import type { UsageRecordsFilters, UsageStatusFilter } from "@/stores/usage-records-store";
import { USAGE_PAGE_SIZE } from "@/stores/usage-records-store";
import { RecordRow } from "./RecordRow";
import { projectTitleResolver, providerLabelResolver, usageProjectLabel } from "./usage-record-format";
import type { UsageRecordView } from "./usage-record-view";

interface UsageRecordsCardProps {
  filters: UsageRecordsFilters;
  summary: UsageSummary | null;
  records: UsageRecordView[];
  inProgress: UsageRecordView[];
  loading: boolean;
  /** 记录请求失败：不渲染「还没有记录」的空态，失败提示由区块统一给出。 */
  failed?: boolean;
  total: number;
  pageIndex: number;
  hasNext: boolean;
  onStatusChange: (status: UsageStatusFilter) => void;
  onPage: (direction: "prev" | "next") => void;
  onOpenDetail: (recordId: number) => void;
  onCancelAll?: () => void;
}

const STATUS_TABS: { value: UsageStatusFilter; labelKey: string }[] = [
  { value: "all", labelKey: "all" },
  { value: "pending", labelKey: "usage_status_pending" },
  { value: "success", labelKey: "usage_status_success" },
  { value: "failed", labelKey: "usage_status_failed" },
  { value: "cancelled", labelKey: "usage_status_cancelled" },
];

export function UsageRecordsCard({
  filters,
  summary,
  records,
  inProgress,
  loading,
  failed = false,
  total,
  pageIndex,
  hasNext,
  onStatusChange,
  onPage,
  onOpenDetail,
  onCancelAll,
}: UsageRecordsCardProps) {
  const { t, i18n } = useTranslation("dashboard");
  const headingId = useId();
  const providerLabel = providerLabelResolver(summary);
  const titleOf = projectTitleResolver(summary);
  const projectLabel = (record: UsageRecordView) =>
    usageProjectLabel(record.projectName, t, i18n.language, record.projectTitle ?? titleOf(record.projectName));
  // 筛选已固定某个项目时隐藏项目列，避免整列重复同一个值。
  const showProject = filters.project === null;
  const columns = showProject ? 9 : 8;
  const from = total === 0 ? 0 : pageIndex * USAGE_PAGE_SIZE + 1;
  const to = pageIndex * USAGE_PAGE_SIZE + records.length;
  const empty = records.length === 0 && inProgress.length === 0 && !loading;
  const showPagination = filters.status !== "pending" && !empty;

  const row = (record: UsageRecordView) => (
    <RecordRow
      key={record.key}
      record={record}
      layout="table"
      showProject={showProject}
      providerLabel={providerLabel}
      projectLabel={projectLabel}
      onOpenDetail={onOpenDetail}
    />
  );

  return (
    <section aria-labelledby={headingId} className="flex flex-col rounded-xl border border-border bg-card">
      <header className="flex flex-wrap items-center gap-2 px-4 py-3">
        <h3 id={headingId} className="text-sm font-medium">
          {t("usage_records_list")}
        </h3>
        <div role="group" aria-label={t("usage_status_filter_label")} className="ml-auto flex items-center gap-1">
          {STATUS_TABS.map((tab) => {
            const active = filters.status === tab.value;
            return (
              <Button
                key={tab.value}
                size="xs"
                variant={active ? "secondary" : "ghost"}
                aria-pressed={active}
                onClick={() => onStatusChange(tab.value)}
              >
                {t(tab.labelKey)}
              </Button>
            );
          })}
        </div>
      </header>

      {empty ? (
        !failed && <p className="px-4 pb-5 text-sm text-muted-foreground">{t("usage_records_empty")}</p>
      ) : (
        <div className="relative overflow-x-auto scroll-fade-x">
          <table className="w-full min-w-3xl table-fixed border-collapse text-sm">
            <colgroup>
              <col className="w-9" />
              {showProject && <col />}
              <col />
              <col />
              <col className="w-36" />
              <col className="w-16" />
              <col className="w-24" />
              <col className="w-20" />
              <col className="w-16" />
            </colgroup>
            <thead>
              <tr className="border-t border-border text-left text-xs font-medium whitespace-nowrap text-muted-foreground">
                <th scope="col" className="py-2 pl-3 font-medium">
                  <span className="sr-only">{t("usage_col_media_type")}</span>
                </th>
                {showProject && (
                  <th scope="col" className="px-2 py-2 font-medium">
                    {t("usage_col_project")}
                  </th>
                )}
                <th scope="col" className="px-2 py-2 font-medium">
                  {t("usage_col_target")}
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  {t("usage_col_model")}
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  {t("usage_col_status")}
                </th>
                <th scope="col" className="px-2 py-2 text-right font-medium">
                  {t("usage_col_duration")}
                </th>
                <th scope="col" className="px-2 py-2 font-medium">
                  {t("usage_col_time")}
                </th>
                <th scope="col" className="px-2 py-2 text-right font-medium">
                  {t("usage_col_cost")}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  <span className="sr-only">{t("usage_row_detail")}</span>
                </th>
              </tr>
            </thead>

            {inProgress.length > 0 && (
              <tbody>
                <tr className="border-t border-border bg-muted/30">
                  <td colSpan={columns} className="px-3 py-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-primary">
                        {t("usage_status_pending")} · {inProgress.length}
                      </span>
                      {onCancelAll && (
                        <Button size="xs" variant="ghost" className="ml-auto" onClick={onCancelAll}>
                          {t("usage_cancel_all")}
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
                {inProgress.map(row)}
              </tbody>
            )}

            <tbody>{records.map(row)}</tbody>
          </table>
        </div>
      )}

      {showPagination && (
        <footer className="flex items-center justify-between border-t border-border px-4 py-2">
          <span className="num text-xs text-muted-foreground">{t("usage_page_position", { from, to, total })}</span>
          <div className="flex items-center gap-1">
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("usage_prev_page")}
              disabled={pageIndex === 0}
              onClick={() => onPage("prev")}
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("usage_next_page")}
              disabled={!hasNext}
              onClick={() => onPage("next")}
            >
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        </footer>
      )}
    </section>
  );
}
