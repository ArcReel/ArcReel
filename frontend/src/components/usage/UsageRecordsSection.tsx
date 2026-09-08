import { useCallback, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearch } from "wouter";
import { useShallow } from "zustand/react/shallow";

import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { isActiveStatus, useTasksStore } from "@/stores/tasks-store";
import {
  parseUsageFilters,
  parseUsageRecordId,
  useUsageRecordsStore,
  writeUsageFilters,
} from "@/stores/usage-records-store";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import type { TaskItem } from "@/types";
import { CancelConfirmDialog } from "./CancelConfirmDialog";
import { UsageAttentionCard } from "./UsageAttentionCard";
import { UsageBreakdownCard } from "./UsageBreakdownCard";
import { UsageFilterBar } from "./UsageFilterBar";
import { UsageKpiStrip } from "./UsageKpiStrip";
import { UsageRecordDetailModal } from "./UsageRecordDetailModal";
import { UsageRecordsCard } from "./UsageRecordsCard";
import { UsageTrendCard } from "./UsageTrendCard";
import { providerLabelResolver } from "./usage-record-format";
import { useTaskCancellation } from "./use-task-cancellation";
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
  // 任务的分镜就是它的资源 id；生成图片、视频、配音的任务都以分镜为目标。
  if (filters.segment !== null && task.resource_id !== filters.segment) return false;
  // 排队中的任务还没解析出模型，按模型筛选时一律算不匹配。
  if (filters.model !== null) return false;
  return true;
}

export function UsageRecordsSection() {
  const { t, i18n } = useTranslation("dashboard");
  const [location, navigate] = useLocation();
  const search = useSearch();

  const parsedFilters = useMemo(() => parseUsageFilters(search), [search]);
  const { range, project, provider, model, mediaType, segment, status } = parsedFilters;
  const filters = useMemo(
    () => ({ range, project, provider, model, mediaType, segment, status }),
    [range, project, provider, model, mediaType, segment, status],
  );
  const recordId = useMemo(() => parseUsageRecordId(search), [search]);

  // 设置页不挂工作台外壳，需在这里登记任务刷新作用域，直达页面也能看到当前进行中任务。
  useTaskRefresh(filters.project);

  const summary = useUsageRecordsStore((s) => s.summary);
  const records = useUsageRecordsStore((s) => s.records);
  const pendingRecords = useUsageRecordsStore((s) => s.pendingRecords);
  const recordsLoading = useUsageRecordsStore((s) => s.recordsLoading);
  const recordsFailed = useUsageRecordsStore((s) => s.recordsFailed);
  const summaryLoading = useUsageRecordsStore((s) => s.summaryLoading);
  const summaryFailed = useUsageRecordsStore((s) => s.summaryFailed);
  const total = useUsageRecordsStore((s) => s.total);
  const pageIndex = useUsageRecordsStore((s) => s.pageIndex);
  const nextCursor = useUsageRecordsStore((s) => s.nextCursor);
  const detailId = useUsageRecordsStore((s) => s.detailId);
  const detail = useUsageRecordsStore((s) => s.detail);
  const detailLoading = useUsageRecordsStore((s) => s.detailLoading);
  const detailFailed = useUsageRecordsStore((s) => s.detailFailed);
  const applyFilters = useUsageRecordsStore((s) => s.applyFilters);
  const refresh = useUsageRecordsStore((s) => s.refresh);
  const refreshSummary = useUsageRecordsStore((s) => s.refreshSummary);
  const refreshPending = useUsageRecordsStore((s) => s.refreshPending);
  const goToPage = useUsageRecordsStore((s) => s.goToPage);
  const openDetail = useUsageRecordsStore((s) => s.openDetail);
  const closeDetail = useUsageRecordsStore((s) => s.closeDetail);

  const activeTasks = useTasksStore(
    useShallow((s) => s.tasks.filter((task) => isActiveStatus(task.status))),
  );

  // 打开与筛选变化时取数；URL 是筛选的真相源，刷新后从 URL 恢复走的是同一条路径。
  // store 跨挂载常驻，打开那次要连 summary 一起重取，离开期间结束的调用才进 KPI 与图表。
  const openedRef = useRef(false);
  useEffect(() => {
    const opened = openedRef.current;
    openedRef.current = true;
    void applyFilters(filters, { refetchSummary: !opened });
  }, [filters, applyFilters]);

  useEffect(() => {
    if (recordId === null) {
      closeDetail();
      return;
    }
    void openDetail(recordId);
  }, [recordId, openDetail, closeDetail]);

  // 供应商显示名随 summary 由服务端按请求语言渲染；切换语言后只重取 summary，
  // 记录页与分页位置不动。挂载那次的取数由上方筛选 effect 负责。
  const languageRef = useRef(i18n.language);
  useEffect(() => {
    if (languageRef.current === i18n.language) return;
    languageRef.current = i18n.language;
    void refreshSummary();
  }, [i18n.language, refreshSummary]);

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

  // 只看「有没有」进行中行：行数变化（任务陆续入队）不该重置计时器，否则轮询会被一直推迟。
  const hasInProgress = inProgress.length > 0;
  useEffect(() => {
    if (!hasInProgress) return;
    const timer = setInterval(() => {
      void refreshPending();
    }, PENDING_POLL_MS);
    return () => clearInterval(timer);
  }, [hasInProgress, refreshPending]);

  // 进行中行消失意味着一次调用已结束（任务终态或 pending 调用落账），记录表、KPI 与图表
  // 要接住它，整体重取一次。只认同一视图（筛选与页码不变）下的收缩：筛选与翻页引起的
  // 消失由各自的取数路径负责。
  const inProgressKeys = useMemo(() => inProgress.map((row) => row.key), [inProgress]);
  const viewKey = `${JSON.stringify(filters)}|${pageIndex}`;
  const lastInProgressRef = useRef<{ viewKey: string; keys: readonly string[] } | null>(null);
  useEffect(() => {
    const previous = lastInProgressRef.current;
    lastInProgressRef.current = { viewKey, keys: inProgressKeys };
    if (previous === null || previous.viewKey !== viewKey) return;
    const current = new Set(inProgressKeys);
    if (previous.keys.some((key) => !current.has(key))) void refresh();
  }, [inProgressKeys, viewKey, refresh]);

  const rows = useMemo(
    () => records.filter((record) => record.status !== "pending").map(usageRecordToView),
    [records],
  );

  const hasAttention = (summary?.attention.length ?? 0) > 0;

  // 「全部取消」只清排队中，与顶栏悬浮层同一套确认流程。项目未固定时无从取消——
  // 取消接口按项目作用，跨项目一次清空不是这个按钮的语义。
  const cancellation = useTaskCancellation(
    filters.project,
    useCallback(
      () => Promise.all([refresh(), useTasksStore.getState().refreshTasks()]).then(() => undefined),
      [refresh],
    ),
  );
  const cancelProject = filters.project;
  const onCancelAll =
    cancelProject === null ||
    activeTasks.every(
      (task) => task.status !== "queued" || !taskMatchesFilters(task, filters),
    )
      ? undefined
      : () => void cancellation.requestAll(cancelProject);

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

      {(summaryFailed || recordsFailed) && (
        <p role="status" className="text-[12px] text-danger-2">
          {t("usage_load_failed")}
        </p>
      )}

      <UsageKpiStrip summary={summary} />

      <UsageTrendCard summary={summary} />

      <div className="grid grid-cols-12 gap-4">
        <UsageBreakdownCard
          summary={summary}
          filters={filters}
          onChange={onFiltersChange}
          wide={!hasAttention}
        />
        {hasAttention && summary && (
          <UsageAttentionCard summary={summary} onChange={onFiltersChange} />
        )}
      </div>

      <UsageRecordsCard
        filters={filters}
        summary={summary}
        records={rows}
        inProgress={inProgress}
        loading={recordsLoading}
        failed={recordsFailed}
        total={total}
        pageIndex={pageIndex}
        hasNext={nextCursor !== null}
        onStatusChange={(status) => onFiltersChange({ status })}
        onPage={(direction) => void goToPage(direction)}
        onOpenDetail={onOpenDetail}
        onCancelAll={onCancelAll}
      />

      {cancellation.request && (
        <CancelConfirmDialog
          request={cancellation.request}
          cancelling={cancellation.cancelling}
          failed={cancellation.failed}
          onConfirm={cancellation.confirm}
          onDismiss={cancellation.dismiss}
        />
      )}

      {recordId !== null && (
        <UsageRecordDetailModal
          recordId={recordId}
          // URL 直接换 id 时 store 要到 effect 才切换，首帧不能把上一条的正文挂在新标题下。
          detail={detailId === recordId ? detail : null}
          loading={detailLoading}
          failed={detailFailed}
          providerLabel={providerLabelResolver(summary)}
          onClose={onCloseDetail}
        />
      )}
    </section>
  );
}
