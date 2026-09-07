import { useCallback, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearch } from "wouter";
import { useShallow } from "zustand/react/shallow";

import { isActiveStatus, useTasksStore } from "@/stores/tasks-store";
import {
  parseUsageFilters,
  parseUsageRecordId,
  useUsageRecordsStore,
  writeUsageFilters,
} from "@/stores/usage-records-store";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import type { TaskItem } from "@/types";
import { UsageFilterBar } from "./UsageFilterBar";
import { UsageKpiStrip } from "./UsageKpiStrip";
import { UsageRecordDetailModal } from "./UsageRecordDetailModal";
import { UsageRecordsCard } from "./UsageRecordsCard";
import { providerLabelResolver } from "./usage-record-format";
import {
  sortByStartedDesc,
  taskToUsageRecordView,
  usageRecordToView,
} from "./usage-record-view";

/** 进行中区非空时的兜底轮询间隔；设置页不订阅事件流。 */
const PENDING_POLL_MS = 3000;

/** 时间范围不作用于进行中区，其余维度同样作用于任务行。 */
function taskMatchesFilters(task: TaskItem, filters: UsageRecordsFilters): boolean {
  if (filters.project !== null && task.project_name !== filters.project) return false;
  if (filters.mediaType !== null && task.media_type !== filters.mediaType) return false;
  if (filters.provider !== null && task.provider_id !== filters.provider) return false;
  // 排队中的任务还没解析出模型，按模型筛选时一律算不匹配。
  if (filters.model !== null) return false;
  return true;
}

export function UsageRecordsSection() {
  const { t } = useTranslation("dashboard");
  const [location, navigate] = useLocation();
  const search = useSearch();

  const parsedFilters = useMemo(() => parseUsageFilters(search), [search]);
  const { range, project, provider, model, mediaType, status } = parsedFilters;
  const filters = useMemo(
    () => ({ range, project, provider, model, mediaType, status }),
    [range, project, provider, model, mediaType, status],
  );
  const recordId = useMemo(() => parseUsageRecordId(search), [search]);

  const summary = useUsageRecordsStore((s) => s.summary);
  const records = useUsageRecordsStore((s) => s.records);
  const pendingRecords = useUsageRecordsStore((s) => s.pendingRecords);
  const recordsLoading = useUsageRecordsStore((s) => s.recordsLoading);
  const summaryLoading = useUsageRecordsStore((s) => s.summaryLoading);
  const total = useUsageRecordsStore((s) => s.total);
  const pageIndex = useUsageRecordsStore((s) => s.pageIndex);
  const nextCursor = useUsageRecordsStore((s) => s.nextCursor);
  const detail = useUsageRecordsStore((s) => s.detail);
  const detailLoading = useUsageRecordsStore((s) => s.detailLoading);
  const detailFailed = useUsageRecordsStore((s) => s.detailFailed);
  const applyFilters = useUsageRecordsStore((s) => s.applyFilters);
  const refresh = useUsageRecordsStore((s) => s.refresh);
  const refreshPending = useUsageRecordsStore((s) => s.refreshPending);
  const goToPage = useUsageRecordsStore((s) => s.goToPage);
  const openDetail = useUsageRecordsStore((s) => s.openDetail);
  const closeDetail = useUsageRecordsStore((s) => s.closeDetail);

  const activeTasks = useTasksStore(
    useShallow((s) => s.tasks.filter((task) => isActiveStatus(task.status))),
  );

  // 打开与筛选变化时取数；URL 是筛选的真相源，刷新后从 URL 恢复走的是同一条路径。
  useEffect(() => {
    void applyFilters(filters);
  }, [filters, applyFilters]);

  useEffect(() => {
    if (recordId === null) {
      closeDetail();
      return;
    }
    void openDetail(recordId);
  }, [recordId, openDetail, closeDetail]);

  const writeQuery = useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(search);
      mutate(params);
      const qs = params.toString();
      // 一律 replace：筛选与详情的每一次微调都压进历史会让「后退」变成逐格倒带。
      navigate(qs ? `${location}?${qs}` : location, { replace: true });
    },
    [search, location, navigate],
  );

  const onFiltersChange = useCallback(
    (patch: Partial<UsageRecordsFilters>) => {
      writeQuery((params) => writeUsageFilters(params, { ...filters, ...patch }));
    },
    [filters, writeQuery],
  );

  const onOpenDetail = useCallback(
    (id: number) => writeQuery((params) => params.set("record", String(id))),
    [writeQuery],
  );

  const onCloseDetail = useCallback(
    () => writeQuery((params) => params.delete("record")),
    [writeQuery],
  );

  // 进行中区 = 任务 store（有任务的调用）∪ 无任务的 pending 调用，两者不相交。
  const showInProgress =
    pageIndex === 0 && (filters.status === "all" || filters.status === "pending");
  const inProgress = useMemo(() => {
    if (!showInProgress) return [];
    return sortByStartedDesc([
      ...activeTasks.filter((task) => taskMatchesFilters(task, filters)).map(taskToUsageRecordView),
      ...pendingRecords.map(usageRecordToView),
    ]);
  }, [showInProgress, activeTasks, filters, pendingRecords]);

  useEffect(() => {
    if (inProgress.length === 0) return;
    const timer = setInterval(() => {
      void refreshPending();
    }, PENDING_POLL_MS);
    return () => clearInterval(timer);
  }, [inProgress.length, refreshPending]);

  const rows = useMemo(
    () => records.filter((record) => record.status !== "pending").map(usageRecordToView),
    [records],
  );

  return (
    <section className="space-y-4">
      <header>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          Usage Records
        </div>
        <h3 className="mt-1 text-[14.5px] font-medium text-text">
          {t("usage_records_title")}
        </h3>
        <p className="mt-1 text-[12px] leading-[1.55] text-text-3">
          {t("usage_records_desc")}
        </p>
      </header>

      <UsageFilterBar
        filters={filters}
        summary={summary}
        onChange={onFiltersChange}
        onRefresh={() => void refresh()}
        refreshing={summaryLoading || recordsLoading}
      />

      <UsageKpiStrip summary={summary} />

      {/* 趋势卡、构成表与需要关注插在这里，与 KPI 条和记录卡同列。 */}

      <UsageRecordsCard
        filters={filters}
        summary={summary}
        records={rows}
        inProgress={inProgress}
        loading={recordsLoading}
        total={total}
        pageIndex={pageIndex}
        hasNext={nextCursor !== null}
        onStatusChange={(status) => onFiltersChange({ status })}
        onPage={(direction) => void goToPage(direction)}
        onOpenDetail={onOpenDetail}
      />

      {recordId !== null && (
        <UsageRecordDetailModal
          recordId={recordId}
          detail={detail}
          loading={detailLoading}
          failed={detailFailed}
          providerLabel={providerLabelResolver(summary)}
          onClose={onCloseDetail}
        />
      )}
    </section>
  );
}
