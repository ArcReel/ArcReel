import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, CheckCircle2, ChevronDown, ChevronRight, CircleStop, LoaderCircle, XCircle } from "lucide-react";
import type { ContentBlock, Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { projectEntriesToTurns } from "@/utils/entry-projection";
import { ContentBlockRenderer } from "./ContentBlockRenderer";
import { getRoleLabel, TERMINAL_SESSION_STATUSES } from "./utils";

// ---------------------------------------------------------------------------
// SubagentCard – single collapsible card for a subagent (Task tool_use).
//
// 默认收起：状态点 + 描述 + 进度元数据。展开可见子时间线（按
// parent_tool_use_id 归组的全量内部消息，左侧 rail + 缩进），实时与
// 历史回放走同一投影，呈现一致。
// ---------------------------------------------------------------------------

interface SubagentCardProps {
  block: ContentBlock;
}

type CardStatus = "running" | "completed" | "failed" | "stopped";

function deriveStatus(block: ContentBlock, sessionDone: boolean): CardStatus {
  const task = block.task_info;
  if (task?.task_status === "failed" || block.is_error) return "failed";
  if (task?.task_status === "completed") return "completed";
  // Agent 工具的 async_launched tool_result 只代表成功派发，不能当作完成。
  return sessionDone && block.result === undefined ? "stopped" : "running";
}

function formatDuration(durationMs: number | undefined): string | null {
  if (durationMs == null || !Number.isFinite(durationMs)) return null;
  const seconds = Math.max(0, Math.round(durationMs / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function deriveDescription(block: ContentBlock): string {
  const input = block.input ?? {};
  const fromInput = typeof input.description === "string" ? input.description : "";
  const fromTask = block.task_info?.description ?? "";
  const fromPrompt = typeof input.prompt === "string" ? input.prompt : "";
  return fromInput || fromTask || fromPrompt;
}

export function SubagentCard({ block }: SubagentCardProps) {
  const { t } = useTranslation("dashboard");
  const [isExpanded, setIsExpanded] = useState(false);
  const detailsId = useId();
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  const liveSnapshot = useAssistantStore((s) => (block.id ? s.subagents[block.id] : undefined));
  const sessionDone = sessionStatus != null && TERMINAL_SESSION_STATUSES.has(sessionStatus);

  const fallbackStatus = deriveStatus(block, sessionDone);
  const status: CardStatus = liveSnapshot
    ? liveSnapshot.status === "completed"
      ? "completed"
      : liveSnapshot.status === "failed"
        ? "failed"
        : liveSnapshot.status === "running"
          ? "running"
          : "stopped"
    : fallbackStatus;
  const description = deriveDescription(block);
  const streamedTurns = useMemo(
    () => liveSnapshot ? projectEntriesToTurns(liveSnapshot.entries) : null,
    [liveSnapshot],
  );
  const subTurns = streamedTurns ?? block.sub_turns ?? [];
  const resultText = typeof block.result === "string" ? block.result : "";
  const expandable = subTurns.length > 0 || resultText.trim() !== "";

  const summary = liveSnapshot?.summary || block.task_info?.summary || "";
  const usage = liveSnapshot?.usage ?? block.task_info?.usage;
  const tokens = usage?.total_tokens;
  const duration = formatDuration(usage?.duration_ms);
  const agentType = liveSnapshot?.agent_type || (typeof block.input?.subagent_type === "string" ? block.input.subagent_type : "");

  const statusLabelKeys: Record<CardStatus, string> = {
    running: "subagent_status_running",
    completed: "subagent_status_completed",
    failed: "subagent_status_failed",
    stopped: "subagent_status_stopped",
  };
  const statusLabel = t(statusLabelKeys[status]);
  const statusColor =
    status === "failed"
      ? "var(--color-danger)"
      : status === "completed"
        ? "var(--color-good)"
        : status === "stopped"
          ? "var(--color-text-4)"
          : "var(--color-accent)";

  const StatusIcon = status === "running"
    ? LoaderCircle
    : status === "completed"
      ? CheckCircle2
      : status === "failed"
        ? XCircle
        : CircleStop;

  const header = (
    <div className="flex min-w-0 flex-1 items-start gap-3">
      <span
        className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
        style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)", color: "var(--color-accent-2)" }}
      >
        <Bot className="h-[18px] w-[18px]" aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.1em]" style={{ color: "var(--color-text-4)" }}>
            {agentType || t("subagent_card_label")}
          </span>
          <span
            className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
            style={{ color: statusColor, background: "oklch(0.16 0.01 265 / 0.55)", border: "1px solid var(--color-hairline-soft)" }}
          >
            <StatusIcon className={`h-3 w-3 ${status === "running" ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />
            {statusLabel}
          </span>
        </span>
        <span className="mt-1 block text-[13px] font-medium leading-5" style={{ color: "var(--color-text)" }}>
          {description || summary || t("subagent_card_label")}
        </span>
        <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
          {status === "running" && <span>{t("subagent_background_active")}</span>}
          {tokens != null && <span className="num">{t("subagent_tokens", { count: tokens })}</span>}
          {duration && <span className="num">{duration}</span>}
        </span>
      </span>
      {expandable && (
        <span className="mt-1 shrink-0" style={{ color: "var(--color-text-4)" }}>
          {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
      )}
    </div>
  );

  return (
    <div
      className="my-3 min-w-0 overflow-hidden rounded-xl"
      style={{
        border: `1px solid ${status === "running" ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"}`,
        background: "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.82), oklch(0.19 0.01 265 / 0.72))",
        boxShadow: status === "running" ? "0 10px 28px -18px var(--color-accent-glow)" : "none",
      }}
    >
      {expandable ? (
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          aria-expanded={isExpanded}
          aria-controls={detailsId}
          className="flex w-full items-start px-3.5 py-3 text-left transition-colors"
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "oklch(1 0 0 / 0.04)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          {header}
        </button>
      ) : (
        <div className="flex w-full items-start px-3.5 py-3">{header}</div>
      )}

      {isExpanded && (
        <div id={detailsId} className="px-3.5 pb-3" style={{ borderTop: "1px solid var(--color-hairline-soft)" }}>
          {subTurns.length > 0 ? (
            <div className="mt-2 ml-1 pl-2.5" style={{ borderLeft: "2px solid var(--color-accent-soft)" }}>
              {subTurns.map((turn, turnIndex) => (
                <SubTimelineTurn key={turn.uuid || `sub-turn-${turnIndex}`} turn={turn} />
              ))}
            </div>
          ) : (
            <pre
              className="num mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all text-[11px]"
              style={{ color: "var(--color-text-2)" }}
            >
              {resultText}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function SubTimelineTurn({ turn }: Readonly<{ turn: Turn }>) {
  const { t } = useTranslation("dashboard");
  const blocks = Array.isArray(turn.content) ? turn.content : [];
  if (blocks.length === 0) return null;
  return (
    <div className="mb-2 min-w-0">
      <div
        className="mb-0.5 text-[9.5px] font-semibold uppercase"
        style={{ color: "var(--color-text-4)", letterSpacing: "0.06em" }}
      >
        {getRoleLabel(turn.type, t)}
      </div>
      <div className="min-w-0 overflow-hidden text-[12px] leading-[1.55]" style={{ color: "var(--color-text-2)" }}>
        {blocks.map((subBlock, index) => (
          <ContentBlockRenderer key={subBlock.id ?? index} block={subBlock} index={index} />
        ))}
      </div>
    </div>
  );
}
