import { useCallback, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearch } from "wouter";
import { useShallow } from "zustand/react/shallow";
import { cn } from "cn";

import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { isActiveStatus, useTasksStore } from "@/stores/tasks-store";
import {
  parseUsageFilters,
  parseUsageRecordId,
  useUsageRecordsStore,
  viewPatch,
  writeUsageFilters,
} from "@/stores/usage-records-store";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import type { TaskItem, UsageView } from "@/types";
import { Button } from "@/components/ui/button";
import { CancelConfirmDialog } from "./CancelConfirmDialog";
import { UsageAttentionCard } from "./UsageAttentionCard";
import { UsageBreakdownCard } from "./UsageBreakdownCard";
import { UsageFilterBar } from "./UsageFilterBar";
import { UsageKpiStrip } from "./UsageKpiStrip";
import { UsageRecordDetailModal } from "./UsageRecordDetailModal";
import { UsageRecordsCard } from "./UsageRecordsCard";
import { UsageSessionsCard } from "./UsageSessionsCard";
import { UsageTrendCard } from "./UsageTrendCard";
import { providerLabelResolver } from "./usage-record-format";
import { useTaskCancellation } from "./use-task-cancellation";
import {
  sortByStartedDesc,
  taskToUsageRecordView,
  usageRecordToView,
} from "./usage-record-view";

/** Fallback polling interval while running regions are nonempty; settings pages subscribe to no event streams. */
const PENDING_POLL_MS = 3000;

const VIEWS: { value: UsageView; labelKey: string }[] = [
  { value: "generation", labelKey: "usage_view_generation" },
  { value: "agent", labelKey: "usage_view_agent" },
];

/** Time ranges exclude running regions; other dimensions also apply to task rows. */
function taskMatchesFilters(task: TaskItem, filters: UsageRecordsFilters): boolean {
  if (filters.project !== null && task.project_name !== filters.project) return false;
  if (filters.mediaType !== null && task.media_type !== filters.mediaType) return false;
  if (filters.provider !== null && task.provider_id !== filters.provider) return false;
  // Task storyboards are resource IDs; image/video/speech generation tasks all target storyboards.
  if (filters.segment !== null && task.resource_id !== filters.segment) return false;
  // Queued tasks lack resolved models (model=null), so model filters treat them as nonmatches.
  if (filters.model !== null && task.model !== filters.model) return false;
  return true;
}

export function UsageRecordsSection() {
  const { t, i18n } = useTranslation("dashboard");
  const [location, navigate] = useLocation();
  const search = useSearch();

  const parsedFilters = useMemo(() => parseUsageFilters(search), [search]);
  const { view, range, project, provider, model, mediaType, segment, status } = parsedFilters;
  const filters = useMemo(
    () => ({ view, range, project, provider, model, mediaType, segment, status }),
    [view, range, project, provider, model, mediaType, segment, status],
  );
  const recordId = useMemo(() => parseUsageRecordId(search), [search]);

  // Settings pages lack workbench shells; register task-refresh scopes here so direct navigation still shows current running tasks.
  useTaskRefresh(filters.project);

  const summary = useUsageRecordsStore((s) => s.summary);
  const records = useUsageRecordsStore((s) => s.records);
  const sessions = useUsageRecordsStore((s) => s.sessions);
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

  // Fetch on opening/filter changes; URLs are filter truth, and refresh restoration follows identical paths.
  // Stores persist across mounts; opening refetches summaries too, incorporating calls completed while absent into KPIs/charts.
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

  // Servers render provider display names with summaries using request languages; language changes refetch summaries only,
  // leaving record pages/pagination unchanged. Filter effects above own mount fetching.
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
      // Always replace history: recording every filter/detail adjustment would make Back rewind field by field.
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

  // Running regions = task stores (task-backed calls) ∪ taskless pending calls, with no overlap.
  const showInProgress =
    filters.view === "generation" &&
    pageIndex === 0 &&
    (filters.status === "all" || filters.status === "pending");
  const inProgress = useMemo(() => {
    if (!showInProgress) return [];
    return sortByStartedDesc([
      ...activeTasks.flatMap((task) => {
        const view = taskMatchesFilters(task, filters) ? taskToUsageRecordView(task) : null;
        return view === null ? [] : [view];
      }),
      ...pendingRecords.map(usageRecordToView),
    ]);
  }, [showInProgress, activeTasks, filters, pendingRecords]);

  // Observe only whether running rows exist; changing counts as tasks queue must not reset timers and indefinitely defer polling.
  const hasInProgress = inProgress.length > 0;
  useEffect(() => {
    if (!hasInProgress) return;
    const timer = setInterval(() => {
      void refreshPending();
    }, PENDING_POLL_MS);
    return () => clearInterval(timer);
  }, [hasInProgress, refreshPending]);

  // Disappearing running rows mean calls ended (terminal tasks/pending calls accounted); refetch entire record tables/KPIs/charts
  // to capture them. Recognize contraction only within unchanged views (filters/page numbers); disappearances from filtering/paging
  // belong to their respective fetch paths.
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

  // Cancel all clears queued tasks only, sharing header-popover confirmation. Unfixed projects cannot cancel because
  // APIs operate per project; cross-project clearing is outside this button meaning.
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
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-medium">{t("usage_records_title")}</h2>
        <p className="text-sm text-muted-foreground">{t("usage_records_desc")}</p>
      </header>

      <div role="group" aria-label={t("usage_view_label")} className="flex items-center gap-1">
        {VIEWS.map((option) => {
          const active = filters.view === option.value;
          return (
            <Button
              key={option.value}
              size="sm"
              variant={active ? "secondary" : "ghost"}
              aria-pressed={active}
              onClick={() => {
                if (!active) onFiltersChange(viewPatch(option.value));
              }}
            >
              {t(option.labelKey)}
            </Button>
          );
        })}
      </div>

      <UsageFilterBar
        filters={filters}
        summary={summary}
        segmentRef={
          filters.segment
            ? (records.find((record) => record.segment_id === filters.segment)?.segment_ref ?? null)
            : null
        }
        onChange={onFiltersChange}
        onRefresh={() => void refresh()}
        refreshing={summaryLoading || recordsLoading}
      />

      {(summaryFailed || recordsFailed) && (
        <p role="status" className="text-sm text-destructive">
          {t("usage_load_failed")}
        </p>
      )}

      <UsageKpiStrip summary={summary} />

      <UsageTrendCard summary={summary} />

      {/* Breakdown/Needs attention sit alongside when content widths permit, otherwise stack; breakdown fills rows without attention items. */}
      <div className="grid grid-cols-1 gap-4 @4xl/page:grid-cols-12">
        <div className={cn("flex min-w-0 flex-col", hasAttention ? "@4xl/page:col-span-7" : "@4xl/page:col-span-12")}>
          <UsageBreakdownCard summary={summary} filters={filters} onChange={onFiltersChange} />
        </div>
        {hasAttention && summary && (
          <div className="flex min-w-0 flex-col @4xl/page:col-span-5">
            <UsageAttentionCard summary={summary} onChange={onFiltersChange} />
          </div>
        )}
      </div>

      {filters.view === "agent" ? (
        <UsageSessionsCard
          filters={filters}
          summary={summary}
          sessions={sessions}
          loading={recordsLoading}
          failed={recordsFailed}
          total={total}
          pageIndex={pageIndex}
          hasNext={nextCursor !== null}
          onPage={(direction) => void goToPage(direction)}
        />
      ) : (
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
      )}

      {cancellation.request && (
        <CancelConfirmDialog
          request={cancellation.request}
          cancelling={cancellation.cancelling}
          failed={cancellation.failed}
          failureDetail={cancellation.failureDetail}
          onConfirm={cancellation.confirm}
          onDismiss={cancellation.dismiss}
        />
      )}

      {recordId !== null && (
        <UsageRecordDetailModal
          recordId={recordId}
          // Direct URL-ID changes update stores only in effects; never render previous bodies under new titles on initial frames.
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
