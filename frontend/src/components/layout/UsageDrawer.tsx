import { useState, useEffect, useCallback, type RefObject } from "react";
import { X, Image, Video, AlertCircle, DollarSign, ChevronLeft, ChevronRight } from "lucide-react";
import { useUsageStore } from "@/stores/usage-store";
import { API } from "@/api";
import { Popover } from "@/components/ui/Popover";

// ---------------------------------------------------------------------------
// UsageDrawer — 费用明细抽屉面板
// ---------------------------------------------------------------------------

interface UsageDrawerProps {
  open: boolean;
  onClose: () => void;
  projectName?: string | null;
  anchorRef: RefObject<HTMLElement | null>;
}

interface UsageCall {
  id: string;
  project_name: string;
  call_type: string;
  model: string;
  status: string;
  cost_amount: number;
  currency: string;
  provider: string;
  output_path: string | null;
  resolution: string | null;
  duration_seconds: number | null;
  duration_ms: number | null;
  error_message: string | null;
  started_at: string;
  created_at: string;
}

export function UsageDrawer({ open, onClose, projectName, anchorRef }: UsageDrawerProps) {
  const { stats, calls, total, page, pageSize, setStats, setCalls, setPage, setLoading } = useUsageStore();
  const [callsLoading, setCallsLoading] = useState(false);

  // 加载费用统计
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    API.getUsageStats(projectName ? { projectName } : {})
      .then((res) => {
        setStats(res as {
          total_cost: number;
          cost_by_currency: Record<string, number>;
          image_count: number;
          video_count: number;
          failed_count: number;
          total_count: number;
        });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, projectName, setStats, setLoading]);

  // 加载调用记录
  const loadCalls = useCallback(() => {
    setCallsLoading(true);
    API.getUsageCalls({
      projectName: projectName ?? undefined,
      page,
      pageSize,
    })
      .then((res) => {
        const r = res as { items?: UsageCall[]; total?: number };
        setCalls((r.items ?? []) as UsageCall[], r.total ?? 0);
      })
      .catch(() => {})
      .finally(() => setCallsLoading(false));
  }, [projectName, page, pageSize, setCalls]);

  useEffect(() => {
    if (open) loadCalls();
  }, [open, loadCalls]);

  const totalPages = Math.ceil(total / pageSize);
  const costByCurrency = stats?.cost_by_currency ?? {};
  const costParts = Object.entries(costByCurrency)
    .filter(([, v]) => v > 0)
    .map(([currency, amount]) => `${currency === "CNY" ? "¥" : "$"}${amount.toFixed(2)}`);
  const costSummary = costParts.length > 0 ? costParts : ["$0.00"];

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      width="w-96"
      className="rounded-xl border border-gray-700 shadow-2xl"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <DollarSign className="h-4 w-4 text-indigo-400" />
          <h3 className="text-sm font-medium text-gray-200">费用明细</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Stats summary */}
      <div className="grid grid-cols-4 gap-2 border-b border-gray-800 px-4 py-3">
        <StatBlock
          label="总费用"
          value={
            costSummary.length === 1
              ? costSummary[0]
              : <span className="flex flex-col items-center leading-tight">{costSummary.map((part, i) => <span key={i}>{i !== 0 && <span className="text-gray-500">+</span>} {part}</span>)}</span>
          }
          accent
        />
        <StatBlock label="图片" value={String(stats?.image_count ?? 0)} icon={<Image className="h-3 w-3 text-blue-400" />} />
        <StatBlock label="视频" value={String(stats?.video_count ?? 0)} icon={<Video className="h-3 w-3 text-purple-400" />} />
        <StatBlock label="失败" value={String(stats?.failed_count ?? 0)} icon={<AlertCircle className="h-3 w-3 text-red-400" />} />
      </div>

      {/* Call records */}
      <div className="max-h-72 overflow-y-auto">
        {callsLoading ? (
          <div className="flex items-center justify-center py-8 text-xs text-gray-500">加载中...</div>
        ) : calls.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-xs text-gray-500">暂无调用记录</div>
        ) : (
          <ul className="divide-y divide-gray-800">
            {calls.map((call) => {
              const filename = extractFilename(call.output_path);
              const typeLabel = call.call_type === "video" ? "视频" : "图片";
              const durationInfo = call.duration_ms
                ? `${(call.duration_ms / 1000).toFixed(1)}s`
                : null;

              return (
                <li key={call.id} className="px-4 py-2.5">
                  {/* Row 1: type + filename + status + cost */}
                  <div className="flex items-center gap-2">
                    <span className="shrink-0">
                      {call.call_type === "video" ? (
                        <Video className="h-3.5 w-3.5 text-purple-400" />
                      ) : (
                        <Image className="h-3.5 w-3.5 text-blue-400" />
                      )}
                    </span>
                    <span className="flex-1 truncate text-xs text-gray-200" title={call.output_path ?? undefined}>
                      {filename || typeLabel}
                    </span>
                    <StatusBadge status={call.status} />
                    <span className={`shrink-0 text-xs font-mono ${call.cost_amount > 0 ? "text-gray-200" : "text-gray-500"}`}>
                      {call.currency === "CNY" ? "¥" : "$"}{call.cost_amount.toFixed(4)}
                    </span>
                  </div>
                  {/* Row 2: model + resolution + duration + time */}
                  <div className="mt-0.5 flex items-center gap-2 pl-5.5 text-[10px] text-gray-500">
                    <span className="truncate">{call.model}</span>
                    {call.resolution && <span>{call.resolution}</span>}
                    {durationInfo && <span>{durationInfo}</span>}
                    <span className="ml-auto shrink-0">{formatDateTime(call.started_at || call.created_at)}</span>
                  </div>
                  {/* Row 3: error message (if failed) */}
                  {call.status === "failed" && call.error_message && (
                    <div className="mt-1 rounded bg-red-500/5 px-2 py-1 pl-5.5 text-[10px] text-red-400 truncate" title={call.error_message}>
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
        <div className="flex items-center justify-between border-t border-gray-800 px-4 py-2">
          <span className="text-[10px] text-gray-500">{total} 条记录</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-800 disabled:opacity-30"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <span className="text-[10px] text-gray-400">{page}/{totalPages}</span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
              className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-800 disabled:opacity-30"
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

function StatBlock({ label, value, icon, accent }: {
  label: string;
  value: React.ReactNode;
  icon?: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="text-center">
      <div className="flex items-center justify-center gap-1">
        {icon}
        <span className={`text-sm font-semibold ${accent ? "text-indigo-400" : "text-gray-200"}`}>
          {value}
        </span>
      </div>
      <span className="text-[10px] text-gray-500">{label}</span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    success: "bg-green-500/10 text-green-400",
    failed: "bg-red-500/10 text-red-400",
    pending: "bg-amber-500/10 text-amber-400",
  };
  const cls = colorMap[status] ?? "bg-gray-500/10 text-gray-400";
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] ${cls}`}>
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
  // e.g. "storyboards/scene_E1S01.png" → "scene_E1S01.png"
  // e.g. "characters/姜月茴.png" → "姜月茴.png"
  const parts = outputPath.split("/");
  return parts.at(-1) ?? "";
}
