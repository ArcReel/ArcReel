import { useCallback, useMemo, useState } from "react";
import type { RefObject } from "react";
import { Activity, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { useShallow } from "zustand/react/shallow";

import { API } from "@/api";
import { GlassPopover } from "@/components/ui/GlassPopover";
import { useAppStore } from "@/stores/app-store";
import { isOccupyingStatus, useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import type { TaskItem, UsageSummary } from "@/types";
import { voidPromise } from "@/utils/async";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { CancelConfirmDialog } from "./CancelConfirmDialog";
import { RecordRow } from "./RecordRow";
import { UsageActiveRow } from "./UsageActiveRow";
import { UsageRecordDetailModal } from "./UsageRecordDetailModal";
import { providerLabelResolver } from "./usage-record-format";
import {
  sortByStartedDesc,
  taskToUsageRecordView,
  usageRecordToView,
} from "./usage-record-view";
import type { UsageRecordView } from "./usage-record-view";
import { useTaskCancellation } from "./use-task-cancellation";

interface UsagePopoverProps {
  projectName: string;
  anchorRef: RefObject<HTMLElement | null>;
  /** 与入口按钮的 `aria-controls` 对应。 */
  panelId: string;
}

/** 双栏与两栏各自的滚动高度：面板不吃满视口，短屏下让位给顶栏与页边。 */
const COLUMNS_MAX_HEIGHT = "min(32rem, 100vh - 14rem)";

/** 下载失败的调用可以就地重试；其余失败码没有可就地补救的动作。 */
const DOWNLOAD_FAILED_CODE = "download_failed";

const SECTION_HEAD_CLS =
  "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";

function KpiCell({
  label,
  value,
  sub,
  first,
}: {
  label: string;
  value: string;
  sub: string;
  first?: boolean;
}) {
  return (
    <div className={"px-3 py-2" + (first ? "" : " border-l border-hairline-soft")}>
      <div className={SECTION_HEAD_CLS}>{label}</div>
      <div className="font-editorial mt-0.5 text-[15px] leading-tight text-text">{value}</div>
      <div className="mt-0.5 text-[9.5px] text-text-4">{sub}</div>
    </div>
  );
}

/** KPI 一行 4 格，口径与入口按钮一致：本项目全部时间。 */
function KpiStrip({ summary }: { summary: UsageSummary | null }) {
  const { t } = useTranslation("dashboard");
  const kpi = summary?.kpi ?? null;
  const primary = summary?.primary_currency ?? null;
  const rate =
    kpi && kpi.success_rate !== null ? `${Math.round(kpi.success_rate * 1000) / 10}%` : "—";
  const otherCosts = Object.entries(kpi?.cost ?? {}).filter(
    ([currency, amount]) => currency !== primary && amount > 0,
  );
  return (
    <div className="grid grid-cols-4 border-b border-hairline-soft">
      <KpiCell
        first
        label={t("usage_kpi_calls")}
        value={kpi ? String(kpi.calls) : "—"}
        sub={kpi ? t("usage_kpi_project_all") : "—"}
      />
      <KpiCell
        label={t("usage_kpi_success_rate")}
        value={rate}
        sub={kpi ? t("usage_kpi_success_count", { count: kpi.success }) : "—"}
      />
      <KpiCell
        label={t("usage_kpi_failed")}
        value={kpi ? String(kpi.failed) : "—"}
        sub={kpi ? t("usage_kpi_cancelled_count", { count: kpi.cancelled }) : "—"}
      />
      <KpiCell
        label={t("usage_kpi_cost")}
        value={
          kpi && primary ? formatCurrencyAmount(primary, kpi.cost[primary] ?? 0) : "—"
        }
        sub={
          otherCosts.length > 0
            ? otherCosts
                .map(([currency, amount]) => `+ ${formatCurrencyAmount(currency, amount)}`)
                .join("  ")
            : "—"
        }
      />
    </div>
  );
}

/**
 * 顶栏用量入口的悬浮层：左栏本项目进行中的调用、右栏最近已结束的调用，顶部是与入口
 * 按钮同口径的 KPI，底部通往设置页的全量记录。
 */
export function UsagePopover({ projectName, anchorRef, panelId }: UsagePopoverProps) {
  const { t } = useTranslation("dashboard");
  const [, navigate] = useLocation();
  const open = useAppStore((s) => s.usagePanelOpen);
  const setOpen = useAppStore((s) => s.setUsagePanelOpen);

  const summary = useUsageHeaderStore((s) => s.summary);
  const recent = useUsageHeaderStore((s) => s.recent);
  const pendingRecords = useUsageHeaderStore((s) => s.pending);
  const detailId = useUsageHeaderStore((s) => s.detailId);
  const detail = useUsageHeaderStore((s) => s.detail);
  const detailLoading = useUsageHeaderStore((s) => s.detailLoading);
  const detailFailed = useUsageHeaderStore((s) => s.detailFailed);
  const openDetail = useUsageHeaderStore((s) => s.openDetail);
  const closeDetail = useUsageHeaderStore((s) => s.closeDetail);
  const refresh = useUsageHeaderStore((s) => s.refresh);

  // 取消中的任务要留在列表里（× 换成 spinner），故按占用谓词取，不用显示谓词。
  const activeTasks = useTasksStore(
    useShallow((s) =>
      s.tasks.filter(
        (task) => task.project_name === projectName && isOccupyingStatus(task.status),
      ),
    ),
  );
  const queuedCount = activeTasks.filter((task) => task.status === "queued").length;

  const cancellation = useTaskCancellation(
    projectName,
    useCallback(
      () => Promise.all([refresh(), useTasksStore.getState().refreshTasks()]).then(() => undefined),
      [refresh],
    ),
  );
  const [retryingIds, setRetryingIds] = useState<ReadonlySet<string>>(new Set());

  const providerLabel = providerLabelResolver(summary);

  const activeRows = useMemo(() => {
    const byKey = new Map<string, TaskItem>();
    const views: UsageRecordView[] = [];
    for (const task of activeTasks) {
      const view = taskToUsageRecordView(task);
      byKey.set(view.key, task);
      views.push(view);
    }
    for (const record of pendingRecords) views.push(usageRecordToView(record));
    return sortByStartedDesc(views).map((view) => ({
      view,
      task: byKey.get(view.key) ?? null,
    }));
  }, [activeTasks, pendingRecords]);

  const finishedRows = useMemo(() => recent.map(usageRecordToView), [recent]);

  // 「没有任何调用」以 summary 为准：它是本项目全部时间的口径，与入口按钮一致。
  const empty =
    summary !== null &&
    summary.kpi.calls === 0 &&
    activeRows.length === 0 &&
    finishedRows.length === 0;

  // 按 task_id 分别记在途：多条下载失败的行可以同时重试，用单个 id 会让先返回的那条
  // 把后点的那条的加载态一并清掉。
  const markRetrying = useCallback((taskId: string, retrying: boolean) => {
    setRetryingIds((prev) => {
      const next = new Set(prev);
      if (retrying) next.add(taskId);
      else next.delete(taskId);
      return next;
    });
  }, []);

  const handleRetryDownload = useCallback(
    async (taskId: string) => {
      markRetrying(taskId, true);
      try {
        await API.retryTaskDownload(taskId);
        await Promise.all([useTasksStore.getState().refreshTasks(), refresh()]);
      } catch {
        // 这一行看不出任何变化（仍是失败态、按钮恢复可用），不给回执用户无从判断究竟是
        // 没点上还是被拒了。属「用户在场、可立即重试的同步失败」，按 store 的分工走 toast。
        useAppStore.getState().pushToast(t("retry_download_failed"), "error");
      } finally {
        markRetrying(taskId, false);
      }
    },
    [markRetrying, refresh, t],
  );

  const viewAllRecords = () => {
    setOpen(false);
    navigate(`~/app/settings?section=usage&u_project=${encodeURIComponent(projectName)}`);
  };

  const viewAllButton = (
    <button
      type="button"
      onClick={viewAllRecords}
      className="focus-ring w-full border-t border-hairline-soft px-4 py-2.5 text-[11.5px] text-text-3 transition-colors hover:bg-[oklch(1_0_0_/_0.03)] hover:text-accent-2"
    >
      {t("usage_view_all_records")}
    </button>
  );

  return (
    <GlassPopover
      open={open}
      onClose={() => setOpen(false)}
      anchorRef={anchorRef}
      sideOffset={6}
      width="w-[40rem]"
    >
      <div id={panelId}>
        <header className="flex items-center gap-2 border-b border-hairline-soft px-4 py-3">
          <span
            aria-hidden="true"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-accent-soft bg-accent-dim text-accent-2"
          >
            <Activity className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="text-[14px] font-semibold tracking-tight text-text">
              {t("usage_records_title")}
            </div>
            <div className="num truncate text-[10px] uppercase tracking-[0.12em] text-text-4">
              Usage · {projectName}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label={t("usage_close_panel")}
            className="focus-ring ml-auto shrink-0 rounded p-1 text-text-4 transition-colors hover:text-text"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </header>

        {empty ? (
          <>
            <p className="px-4 py-5 text-[12px] leading-[1.6] text-text-3">
              {t("usage_popover_empty")}
            </p>
            {viewAllButton}
          </>
        ) : (
          <>
            <KpiStrip summary={summary} />
            <div className="flex" style={{ maxHeight: COLUMNS_MAX_HEIGHT }}>
              <section className="flex w-[18rem] shrink-0 flex-col border-r border-hairline-soft">
                <div className="flex items-center gap-2 px-3 py-2">
                  <h4 className={SECTION_HEAD_CLS}>{t("usage_in_progress")}</h4>
                  <span className="num text-[10px] text-text-4">{activeRows.length}</span>
                  {queuedCount > 0 && (
                    <button
                      type="button"
                      onClick={voidPromise(() => cancellation.requestAll(projectName))}
                      className="focus-ring ml-auto rounded px-1 text-[10.5px] text-text-3 transition-colors hover:text-danger-2"
                      aria-label={t("cancel_all_queued_aria")}
                    >
                      {t("cancel_all")}
                    </button>
                  )}
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  {activeRows.length === 0 ? (
                    <p className="px-3 pb-3 text-[11px] leading-[1.6] text-text-4">
                      {t("usage_no_in_progress")}
                    </p>
                  ) : (
                    activeRows.map(({ view, task }) => (
                      <UsageActiveRow
                        key={view.key}
                        view={view}
                        task={task}
                        providerLabel={providerLabel}
                        onCancel={voidPromise(cancellation.requestSingle)}
                        cancelling={
                          task !== null &&
                          (task.status === "cancelling" ||
                            cancellation.cancellingTaskIds.has(task.task_id))
                        }
                      />
                    ))
                  )}
                </div>
              </section>

              <section className="flex min-w-0 flex-1 flex-col">
                <div className="px-3 py-2">
                  <h4 className={SECTION_HEAD_CLS}>{t("usage_recently_finished")}</h4>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  {finishedRows.length === 0 ? (
                    <p className="px-3 pb-3 text-[11px] leading-[1.6] text-text-4">
                      {t("usage_no_finished")}
                    </p>
                  ) : (
                    finishedRows.map((record) => (
                      <RecordRow
                        key={record.key}
                        record={record}
                        layout="compact"
                        providerLabel={providerLabel}
                        onOpenDetail={voidPromise(openDetail)}
                        trailing={
                          record.status === "failed" &&
                          record.errorCode === DOWNLOAD_FAILED_CODE &&
                          record.taskId !== null ? (
                            <RetryDownloadButton
                              taskId={record.taskId}
                              retrying={retryingIds.has(record.taskId)}
                              onRetry={handleRetryDownload}
                            />
                          ) : undefined
                        }
                      />
                    ))
                  )}
                </div>
              </section>
            </div>
            {viewAllButton}
          </>
        )}

        {cancellation.request && (
          <CancelConfirmDialog
            request={cancellation.request}
            cancelling={cancellation.cancelling}
            onConfirm={cancellation.confirm}
            onDismiss={cancellation.dismiss}
          />
        )}
      </div>

      {detailId !== null && (
        <UsageRecordDetailModal
          recordId={detailId}
          detail={detail}
          loading={detailLoading}
          failed={detailFailed}
          providerLabel={providerLabel}
          onClose={closeDetail}
        />
      )}
    </GlassPopover>
  );
}

function RetryDownloadButton({
  taskId,
  retrying,
  onRetry,
}: {
  taskId: string;
  retrying: boolean;
  onRetry: (taskId: string) => Promise<void>;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <button
      type="button"
      disabled={retrying}
      onClick={voidPromise(() => onRetry(taskId))}
      className="focus-ring inline-flex items-center gap-1 rounded px-1 text-[10.5px] text-accent-2 disabled:opacity-60"
    >
      {retrying && <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />}
      {retrying ? t("retrying_download") : t("retry_download")}
    </button>
  );
}
