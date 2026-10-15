import { ChevronRight } from "lucide-react";
import { Fragment, useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { UsageAgentSession, UsageAgentSessionRecord, UsageSummary } from "@/types";
import { USAGE_PAGE_SIZE } from "@/stores/usage-records-store";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import { formatShortDateTime } from "@/utils/date-format";
import { UsagePager } from "./UsagePager";
import {
  STATUS_LABEL_KEYS,
  STATUS_TEXT_CLASSES,
  formatCount,
  formatTokenAmount,
  projectTitleResolver,
  unpricedReasonKey,
  usageProjectLabel,
} from "./usage-record-format";

interface UsageSessionsCardProps {
  filters: UsageRecordsFilters;
  summary: UsageSummary | null;
  sessions: UsageAgentSession[];
  loading: boolean;
  /** 会话请求失败：不渲染空态，失败提示由区块统一给出。 */
  failed?: boolean;
  total: number;
  pageIndex: number;
  hasNext: boolean;
  onPage: (direction: "prev" | "next") => void;
}

/** 无值时的占位。 */
const DASH = "—";

/** 带说明的文字：悬停或聚焦时显示说明；说明只靠悬停无法被键盘与读屏获知，故触发元素可聚焦。 */
function HintedText({ hint, children }: { hint: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 说明须可被键盘与读屏获知；它没有可执行的动作，不渲染为 button
          <span tabIndex={0} className="cursor-help underline decoration-dotted underline-offset-4" />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}

/** 已定价部分的合计：按币种分别写出；全是 0 元时写 0，没有任何已定价记录时写破折号。 */
function costText(cost: Record<string, number>): string {
  const entries = costEntries(cost);
  if (entries.length > 0) {
    return entries.map(([currency, amount]) => formatCurrencyAmount(currency, amount, { maximumFractionDigits: 4 })).join(" + ");
  }
  const [currency] = Object.keys(cost);
  return currency ? formatCurrencyAmount(currency, 0, { maximumFractionDigits: 4 }) : DASH;
}

export function UsageSessionsCard({
  filters,
  summary,
  sessions,
  loading,
  failed = false,
  total,
  pageIndex,
  hasNext,
  onPage,
}: UsageSessionsCardProps) {
  const { t, i18n } = useTranslation("dashboard");
  const headingId = useId();
  const titleOf = projectTitleResolver(summary);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  // 筛选已固定某个项目时隐藏项目列，避免整列重复同一个值。
  const showProject = filters.project === null;
  const columns = showProject ? 6 : 5;
  const from = total === 0 ? 0 : pageIndex * USAGE_PAGE_SIZE + 1;
  const to = pageIndex * USAGE_PAGE_SIZE + sessions.length;
  const empty = sessions.length === 0 && !loading;

  const toggle = (sessionId: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(sessionId)) next.add(sessionId);
      return next;
    });

  const sessionTitle = (session: UsageAgentSession) =>
    session.session_deleted ? t("usage_session_deleted") : session.title || t("usage_session_untitled");

  return (
    <section aria-labelledby={headingId} className="flex flex-col rounded-xl border border-border bg-card">
      <header className="flex flex-wrap items-center gap-2 px-4 py-3">
        <h3 id={headingId} className="text-sm font-medium">
          {t("usage_view_agent")}
        </h3>
      </header>

      {empty ? (
        !failed && <p className="px-4 pb-5 text-sm text-muted-foreground">{t("usage_sessions_empty")}</p>
      ) : (
        <div className="relative overflow-x-auto scroll-fade-x">
          <table className="w-full min-w-3xl table-fixed border-collapse text-sm">
            <colgroup>
              <col />
              {showProject && <col className="w-36" />}
              <col />
              <col className="w-32" />
              <col className="w-32" />
              <col className="w-28" />
            </colgroup>
            <thead>
              <tr className="border-t border-border text-left text-xs font-medium whitespace-nowrap text-muted-foreground">
                <th scope="col" className="py-2 pl-3 font-medium">
                  {t("usage_col_session")}
                </th>
                {showProject && (
                  <th scope="col" className="px-2 py-2 font-medium">
                    {t("usage_col_project")}
                  </th>
                )}
                <th scope="col" className="px-2 py-2 font-medium">
                  {t("usage_col_model")}
                </th>
                <th scope="col" className="px-2 py-2 text-right font-medium">
                  {t("usage_col_usage")}
                </th>
                <th scope="col" className="px-2 py-2 text-right font-medium">
                  {t("usage_col_cost")}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t("usage_col_time")}
                </th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => {
                const title = sessionTitle(session);
                const open = expanded.has(session.session_id);
                const detailId = `${headingId}-${session.session_id}`;
                return (
                  <Fragment key={session.session_id}>
                    <tr className="border-t border-border transition-colors hover:bg-muted/30">
                      <td className="py-2 pr-2 pl-2">
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-controls={detailId}
                          aria-label={t(open ? "usage_session_collapse" : "usage_session_expand", { title })}
                          onClick={() => toggle(session.session_id)}
                          className="flex w-full min-w-0 items-center gap-1 rounded-md px-1 py-0.5 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                        >
                          <ChevronRight
                            aria-hidden="true"
                            className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
                          />
                          <TruncatedText
                            text={title}
                            focusable={false}
                            className={session.session_deleted ? "text-muted-foreground" : "text-foreground"}
                          />
                        </button>
                      </td>
                      {showProject && (
                        <td className="px-2 py-2">
                          <TruncatedText
                            text={usageProjectLabel(
                              session.project_name,
                              t,
                              i18n.language,
                              session.project_title ?? titleOf(session.project_name),
                            )}
                          />
                        </td>
                      )}
                      <td className="px-2 py-2">
                        <ul className="flex flex-col">
                          {session.models.map((model) => (
                            <li key={model} className="min-w-0">
                              <TruncatedText text={model} className="num text-xs text-subtle-foreground" />
                            </li>
                          ))}
                        </ul>
                      </td>
                      <td className="num px-2 py-2 text-right text-xs whitespace-nowrap text-subtle-foreground">
                        {formatTokenAmount(session.input_tokens + session.output_tokens, i18n.language, t)}
                      </td>
                      <td className="num px-2 py-2 text-right text-xs whitespace-nowrap">
                        {costText(session.cost)}
                        {session.unpriced_calls > 0 && (
                          <span className="block text-muted-foreground">{t("usage_session_partly_unpriced")}</span>
                        )}
                      </td>
                      <td className="num py-2 pr-3 text-xs whitespace-nowrap text-muted-foreground">
                        {formatShortDateTime(session.last_started_at) ?? DASH}
                      </td>
                    </tr>
                    {open && (
                      <tr id={detailId} className="bg-muted/20">
                        <td colSpan={columns} className="px-3 pb-3">
                          {/* 列表刷新后最近一轮的时间变了，明细随之重取 */}
                          <SessionDetail
                            key={session.last_started_at}
                            sessionId={session.session_id}
                            title={title}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!empty && (
        <UsagePager from={from} to={to} total={total} hasPrev={pageIndex > 0} hasNext={hasNext} onPage={onPage} />
      )}
    </section>
  );
}

type DetailState =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; items: UsageAgentSessionRecord[] };

/** 展开的会话明细：按时间每条记录一行，同一轮的几条记录自然排在一起。 */
function SessionDetail({ sessionId, title }: { sessionId: string; title: string }) {
  const { t, i18n } = useTranslation("dashboard");
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [openRows, setOpenRows] = useState<ReadonlySet<number>>(new Set());

  useEffect(() => {
    const controller = new AbortController();
    API.getUsageSession(sessionId, { signal: controller.signal }).then(
      (detail) => {
        if (!controller.signal.aborted) setState({ status: "ready", items: detail.items });
      },
      () => {
        if (!controller.signal.aborted) setState({ status: "failed" });
      },
    );
    return () => controller.abort();
  }, [sessionId]);

  if (state.status === "loading") {
    return <p className="py-2 text-xs text-muted-foreground">{t("usage_session_detail_loading")}</p>;
  }
  if (state.status === "failed") {
    return (
      <p role="status" className="py-2 text-xs text-destructive">
        {t("usage_session_detail_failed")}
      </p>
    );
  }

  const show = (value: number | null) => (value === null ? DASH : formatCount(value, i18n.language));
  const toggleRow = (id: number) =>
    setOpenRows((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  return (
    <table
      aria-label={t("usage_session_detail_label", { title })}
      className="w-full table-fixed border-collapse text-xs"
    >
      <colgroup>
        <col className="w-28" />
        <col />
        <col className="w-36" />
        <col className="w-16" />
        <col className="w-20" />
        <col className="w-20" />
        <col className="w-16" />
        <col className="w-24" />
        <col className="w-14" />
      </colgroup>
      <thead>
        <tr className="text-left font-medium whitespace-nowrap text-muted-foreground">
          <th scope="col" className="px-2 py-1.5 font-medium">
            {t("usage_col_time")}
          </th>
          <th scope="col" className="px-2 py-1.5 font-medium">
            {t("usage_col_message")}
          </th>
          <th scope="col" className="px-2 py-1.5 font-medium">
            {t("usage_col_model")}
          </th>
          <th scope="col" className="px-2 py-1.5 text-right font-medium">
            <HintedText hint={t("usage_field_input_tokens_hint")}>{t("usage_field_input_tokens")}</HintedText>
          </th>
          <th scope="col" className="px-2 py-1.5 text-right font-medium">
            {t("usage_field_cache_read_tokens")}
          </th>
          <th scope="col" className="px-2 py-1.5 text-right font-medium">
            {t("usage_field_cache_write_tokens")}
          </th>
          <th scope="col" className="px-2 py-1.5 text-right font-medium">
            {t("usage_field_output_tokens")}
          </th>
          <th scope="col" className="px-2 py-1.5 text-right font-medium">
            {t("usage_col_cost")}
          </th>
          <th scope="col" className="px-2 py-1.5 font-medium">
            <span className="sr-only">{t("usage_row_detail")}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {state.items.map((record) => {
          const {
            input_tokens: input,
            cache_read_tokens: cacheRead,
            cache_write_5m_tokens: write5m,
            cache_write_1h_tokens: write1h,
          } = record;
          const hasWrite = write5m !== null || write1h !== null;
          // 记录里的总输入含缓存读写；对外的「输入」只指没有命中也没有写入缓存的那部分。
          const uncachedInput =
            input === null ? null : Math.max(0, input - (cacheRead ?? 0) - (write5m ?? 0) - (write1h ?? 0));
          const open = openRows.has(record.id);
          return (
            <Fragment key={record.id}>
              <tr className="border-t border-border/60">
                <td className="num px-2 py-1.5 whitespace-nowrap text-muted-foreground">
                  {formatShortDateTime(record.started_at) ?? DASH}
                </td>
                <td className="px-2 py-1.5">
                  <TruncatedText text={record.prompt ?? DASH} />
                </td>
                <td className="px-2 py-1.5">
                  <TruncatedText text={record.model} className="num text-subtle-foreground" />
                </td>
                <td className="num px-2 py-1.5 text-right">{show(uncachedInput)}</td>
                <td className="num px-2 py-1.5 text-right">{show(cacheRead)}</td>
                <td className="num px-2 py-1.5 text-right">
                  {hasWrite ? formatCount((write5m ?? 0) + (write1h ?? 0), i18n.language) : DASH}
                </td>
                <td className="num px-2 py-1.5 text-right">{show(record.output_tokens)}</td>
                <td className="num px-2 py-1.5 text-right whitespace-nowrap">
                  {record.cost_amount === null ? (
                    <HintedText hint={t(unpricedReasonKey(record.unpriced_reason))}>
                      {t("usage_cost_unpriced")}
                    </HintedText>
                  ) : (
                    formatCurrencyAmount(record.currency, record.cost_amount, { maximumFractionDigits: 4 })
                  )}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <Button
                    variant="ghost"
                    size="xs"
                    aria-expanded={open}
                    onClick={() => toggleRow(record.id)}
                  >
                    {t("usage_row_detail")}
                  </Button>
                </td>
              </tr>
              {open && (
                <tr>
                  <td colSpan={9} className="num px-2 pb-2 text-muted-foreground">
                    <span className="mr-4">
                      {t("usage_field_cache_write_tokens")} {t("usage_field_cache_write_5m_tokens")}{" "}
                      <span className="text-subtle-foreground">{show(write5m)}</span>
                    </span>
                    <span className="mr-4">
                      {t("usage_field_cache_write_tokens")} {t("usage_field_cache_write_1h_tokens")}{" "}
                      <span className="text-subtle-foreground">{show(write1h)}</span>
                    </span>
                    {record.status !== "success" && (
                      <span className={STATUS_TEXT_CLASSES[record.status]}>{t(STATUS_LABEL_KEYS[record.status])}</span>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
