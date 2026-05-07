import { useState, useEffect, useCallback, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import {
  X,
  Image,
  Video,
  FileText,
  AlertCircle,
  DollarSign,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useUsageStore, type UsageStats, type UsageCall } from "@/stores/usage-store";
import { API } from "@/api";
import { Popover } from "@/components/ui/Popover";
import type { CallType } from "@/types/provider";

// ---------------------------------------------------------------------------
// UsageDrawer — v3 视觉：玻璃面板 + accent purple + display-serif 标题
// ---------------------------------------------------------------------------

interface UsageDrawerProps {
  open: boolean;
  onClose: () => void;
  projectName?: string | null;
  anchorRef: RefObject<HTMLElement | null>;
}

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

const TYPE_TONE: Record<CallType, { color: string; label: string }> = {
  video: { color: "oklch(0.78 0.13 305)", label: "video_type_label" },
  text: { color: "oklch(0.78 0.10 155)", label: "text_type_label" },
  image: { color: "oklch(0.80 0.10 230)", label: "image_type_label" },
};

const TYPE_ICON: Record<CallType, typeof Image> = {
  video: Video,
  text: FileText,
  image: Image,
};

export function UsageDrawer({ open, onClose, projectName, anchorRef }: UsageDrawerProps) {
  const { t } = useTranslation("dashboard");
  const {
    stats,
    calls,
    total,
    page,
    pageSize,
    setStats,
    setCalls,
    setPage,
    setLoading,
  } = useUsageStore();
  const [callsLoading, setCallsLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    API.getUsageStats(projectName ? { projectName } : {})
      .then((res) => {
        setStats(res as unknown as UsageStats);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, projectName, setStats, setLoading]);

  const loadCalls = useCallback(() => {
    setCallsLoading(true);
    API.getUsageCalls({
      projectName: projectName ?? undefined,
      page,
      pageSize,
    })
      .then((res) => {
        const r = res as { items?: UsageCall[]; total?: number };
        setCalls(r.items ?? [], r.total ?? 0);
      })
      .catch(() => {})
      .finally(() => setCallsLoading(false));
  }, [projectName, page, pageSize, setCalls]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 抽屉打开时触发数据加载，loadCalls 内部有 setState，属于正常异步数据获取模式
    if (open) loadCalls();
  }, [open, loadCalls]);

  const totalPages = Math.ceil(total / pageSize);
  const costByCurrency = stats?.cost_by_currency ?? {};
  const costParts = Object.entries(costByCurrency)
    .filter(([, v]) => v > 0)
    .map(
      ([currency, amount]) =>
        `${currency === "CNY" ? "¥" : "$"}${amount.toFixed(2)}`,
    );
  const costSummary = costParts.length > 0 ? costParts : ["$0.00"];

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      width="w-[26rem]"
      backgroundColor="transparent"
      className="overflow-hidden rounded-xl"
      style={{
        background: PANEL_BG,
        border: "1px solid var(--color-hairline)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        boxShadow:
          "0 24px 60px -12px oklch(0 0 0 / 0.55), inset 0 1px 0 oklch(1 0 0 / 0.05)",
      }}
    >
      {/* Header */}
      <div
        className="relative flex items-center gap-2 px-4 py-3"
        style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
          }}
        />
        <span
          aria-hidden
          className="grid h-7 w-7 place-items-center rounded-lg"
          style={{
            background:
              "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
            boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
          }}
        >
          <DollarSign className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0">
          <div
            className="display-serif text-[14px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("cost_details")}
          </div>
          <div
            className="num text-[10px] uppercase"
            style={{
              color: "var(--color-text-4)",
              letterSpacing: "1.2px",
            }}
          >
            {t("cost_details_eyebrow")}
          </div>
        </div>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors"
          style={{ color: "var(--color-text-3)" }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--color-text)";
            e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-3)";
            e.currentTarget.style.background = "transparent";
          }}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Stats summary */}
      <div
        className="grid grid-cols-5 gap-2 px-4 py-3"
        style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
      >
        <StatBlock
          label={t("total_cost")}
          value={
            costSummary.length === 1 ? (
              costSummary[0]
            ) : (
              <span className="flex flex-col items-center leading-tight">
                {costSummary.map((part, i) => (
                  <span key={i}>
                    {i !== 0 && <span style={{ color: "var(--color-text-4)" }}>+</span>}{" "}
                    {part}
                  </span>
                ))}
              </span>
            )
          }
          accent
        />
        <StatBlock
          label={t("image_type_label")}
          value={String(stats?.image_count ?? 0)}
          icon={<Image className="h-3 w-3" style={{ color: TYPE_TONE.image.color }} />}
        />
        <StatBlock
          label={t("video_type_label")}
          value={String(stats?.video_count ?? 0)}
          icon={<Video className="h-3 w-3" style={{ color: TYPE_TONE.video.color }} />}
        />
        <StatBlock
          label={t("text_type_label")}
          value={String(stats?.text_count ?? 0)}
          icon={<FileText className="h-3 w-3" style={{ color: TYPE_TONE.text.color }} />}
        />
        <StatBlock
          label={t("failed_type_label")}
          value={String(stats?.failed_count ?? 0)}
          icon={
            <AlertCircle
              className="h-3 w-3"
              style={{ color: "oklch(0.72 0.18 25)" }}
            />
          }
        />
      </div>

      {/* Call records */}
      <div className="max-h-72 overflow-y-auto">
        {callsLoading ? (
          <div
            className="flex items-center justify-center py-8 text-[11px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("common:loading")}
          </div>
        ) : calls.length === 0 ? (
          <div
            className="flex items-center justify-center py-8 text-[11px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("no_call_records")}
          </div>
        ) : (
          <ul>
            {calls.map((call) => {
              const filename = extractFilename(call.output_path);
              const tone = TYPE_TONE[call.call_type] ?? TYPE_TONE.image;
              const TypeIcon = TYPE_ICON[call.call_type] ?? Image;
              const durationInfo = call.duration_ms
                ? `${(call.duration_ms / 1000).toFixed(1)}s`
                : null;

              return (
                <li
                  key={call.id}
                  className="px-4 py-2.5 transition-colors"
                  style={{
                    borderTop: "1px solid var(--color-hairline-soft)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "oklch(0.24 0.012 265 / 0.45)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span className="shrink-0">
                      <TypeIcon
                        className="h-3.5 w-3.5"
                        style={{ color: tone.color }}
                      />
                    </span>
                    <span
                      className="flex-1 truncate text-[12px]"
                      style={{ color: "var(--color-text)" }}
                      title={call.output_path ?? undefined}
                    >
                      {filename || t(tone.label)}
                    </span>
                    <StatusBadge status={call.status} />
                    <span
                      className="num shrink-0 text-[11px]"
                      style={{
                        color:
                          call.cost_amount > 0
                            ? "var(--color-text)"
                            : "var(--color-text-4)",
                        fontWeight: call.cost_amount > 0 ? 600 : 400,
                      }}
                    >
                      {call.currency === "CNY" ? "¥" : "$"}
                      {call.cost_amount.toFixed(4)}
                    </span>
                  </div>
                  <div
                    className="mt-1 flex items-center gap-2 pl-5 text-[10px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    <span className="num truncate">{call.model}</span>
                    {call.call_type === "text" ? (
                      <>
                        {call.input_tokens != null && (
                          <span className="num">
                            {t("input_token_label")}{" "}
                            {call.input_tokens.toLocaleString()}
                          </span>
                        )}
                        {call.output_tokens != null && (
                          <span className="num">
                            {t("output_token_label")}{" "}
                            {call.output_tokens.toLocaleString()} tokens
                          </span>
                        )}
                      </>
                    ) : (
                      <>
                        {call.resolution && (
                          <span className="num">{call.resolution}</span>
                        )}
                        {durationInfo && <span className="num">{durationInfo}</span>}
                      </>
                    )}
                    <span className="num ml-auto shrink-0">
                      {formatDateTime(call.started_at || call.created_at)}
                    </span>
                  </div>
                  {call.status === "failed" && call.error_message && (
                    <div
                      className="mt-1 truncate rounded px-2 py-1 pl-5 text-[10px]"
                      style={{
                        background: "oklch(0.30 0.10 25 / 0.10)",
                        color: "oklch(0.85 0.10 25)",
                        border: "1px solid oklch(0.45 0.18 25 / 0.30)",
                      }}
                      title={call.error_message}
                    >
                      {call.error_message}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div
          className="flex items-center justify-between px-4 py-2"
          style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            className="num text-[10px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("records_count", { count: total })}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-30"
              style={{ color: "var(--color-text-3)" }}
              onMouseEnter={(e) => {
                if (page > 1) {
                  e.currentTarget.style.color = "var(--color-text)";
                  e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-text-3)";
                e.currentTarget.style.background = "transparent";
              }}
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <span
              className="num text-[10.5px]"
              style={{ color: "var(--color-text-3)" }}
            >
              {page}/{totalPages}
            </span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
              className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-30"
              style={{ color: "var(--color-text-3)" }}
              onMouseEnter={(e) => {
                if (page < totalPages) {
                  e.currentTarget.style.color = "var(--color-text)";
                  e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-text-3)";
                e.currentTarget.style.background = "transparent";
              }}
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatBlock({
  label,
  value,
  icon,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  icon?: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="text-center">
      <div className="flex items-center justify-center gap-1">
        {icon}
        <span
          className="num text-[13px] font-semibold"
          style={{
            color: accent ? "var(--color-accent-2)" : "var(--color-text)",
          }}
        >
          {value}
        </span>
      </div>
      <span
        className="text-[10px] uppercase"
        style={{
          color: "var(--color-text-4)",
          letterSpacing: "0.6px",
        }}
      >
        {label}
      </span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { color: string; bg: string }> = {
    success: {
      color: "var(--color-good)",
      bg: "oklch(0.30 0.10 155 / 0.18)",
    },
    failed: {
      color: "oklch(0.85 0.10 25)",
      bg: "oklch(0.30 0.10 25 / 0.18)",
    },
    pending: {
      color: "oklch(0.85 0.13 75)",
      bg: "oklch(0.30 0.10 75 / 0.18)",
    },
  };
  const cfg = config[status] ?? {
    color: "var(--color-text-3)",
    bg: "oklch(0.24 0.012 265 / 0.45)",
  };
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[10px] uppercase"
      style={{
        color: cfg.color,
        background: cfg.bg,
        letterSpacing: "0.4px",
      }}
    >
      {status}
    </span>
  );
}

function formatDateTime(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    return `${(d.getMonth() + 1).toString().padStart(2, "0")}/${d.getDate().toString().padStart(2, "0")} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  } catch {
    return isoStr;
  }
}

function extractFilename(outputPath: string | null | undefined): string {
  if (!outputPath) return "";
  const parts = outputPath.split("/");
  return parts.at(-1) ?? "";
}
