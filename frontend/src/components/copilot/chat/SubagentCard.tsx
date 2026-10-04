import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContentBlock, Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
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
  if (task?.task_status === "completed" || block.result !== undefined) return "completed";
  // 缺锚点子智能体的推断终态：子时间线停在工具调用中途
  if (task?.task_status === "stopped") return "stopped";
  // 会话终结且子智能体无终态时，卡片显示已停止，避免运行状态悬挂。
  return sessionDone ? "stopped" : "running";
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
  const sessionDone = sessionStatus != null && TERMINAL_SESSION_STATUSES.has(sessionStatus);

  const status = deriveStatus(block, sessionDone);
  const description = deriveDescription(block);
  const subTurns = block.sub_turns ?? [];
  const resultText = typeof block.result === "string" ? block.result : "";
  const expandable = subTurns.length > 0 || resultText.trim() !== "";

  const summary = block.task_info?.summary ?? "";
  const tokens = block.task_info?.usage?.total_tokens;
  const agentType = typeof block.input?.subagent_type === "string" ? block.input.subagent_type : "";

  const statusLabelKeys: Record<CardStatus, string> = {
    running: "subagent_status_running",
    completed: "subagent_status_completed",
    failed: "subagent_status_failed",
    stopped: "subagent_status_stopped",
  };
  const statusLabel = t(statusLabelKeys[status]);
  const statusColor =
    status === "failed"
      ? "var(--destructive)"
      : status === "completed"
        ? "var(--good)"
        : status === "stopped"
          ? "var(--muted-foreground)"
          : "var(--primary)";

  const header = (
    <>
      <span className="shrink-0" aria-hidden="true">
        {status === "running" ? (
          <span
            className="inline-block h-3 w-3 rounded-full border-t-transparent motion-safe:animate-spin"
            style={{ border: "1px solid var(--primary)", borderTopColor: "transparent" }}
          />
        ) : (
          <span className="text-xs font-medium" style={{ color: statusColor }}>
            {status === "completed" ? "✓" : status === "failed" ? "✗" : "■"}
          </span>
        )}
      </span>
      <span
        className="shrink-0 text-[10px] font-semibold uppercase tracking-wide"
        style={{ color: "var(--muted-foreground)" }}
      >
        {agentType || t("subagent_card_label")}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px]" style={{ color: "var(--subtle-foreground)" }}>
        {description || summary || t("subagent_card_label")}
      </span>
      <span className="ml-1.5 flex shrink-0 items-center gap-1.5">
        {tokens != null && status === "running" && (
          <span className="num text-[10px]" style={{ color: "var(--muted-foreground)" }}>
            {t("subagent_tokens", { count: tokens })}
          </span>
        )}
        <span className="text-[10px]" style={{ color: statusColor }}>
          {statusLabel}
        </span>
        {expandable && (
          <span className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
            {isExpanded ? "▼" : "▶"}
          </span>
        )}
      </span>
    </>
  );

  return (
    <div
      className="my-1.5 min-w-0 overflow-hidden rounded-lg"
      style={{ border: "1px solid color-mix(in oklab, var(--border) 50%, transparent)", background: "oklch(0.21 0.012 265 / 0.5)" }}
    >
      {expandable ? (
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          aria-expanded={isExpanded}
          aria-controls={detailsId}
          className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left transition-colors"
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
        <div className="flex w-full items-center gap-1.5 px-2.5 py-1.5">{header}</div>
      )}

      {isExpanded && (
        <div id={detailsId} className="px-2.5 pb-2" style={{ borderTop: "1px solid color-mix(in oklab, var(--border) 50%, transparent)" }}>
          {subTurns.length > 0 ? (
            <div className="mt-2 ml-1 pl-2.5" style={{ borderLeft: "2px solid color-mix(in oklab, var(--primary) 22%, transparent)" }}>
              {subTurns.map((turn, turnIndex) => (
                <SubTimelineTurn key={turn.uuid || `sub-turn-${turnIndex}`} turn={turn} />
              ))}
            </div>
          ) : (
            <pre
              className="num mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all text-[11px]"
              style={{ color: "var(--subtle-foreground)" }}
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
        style={{ color: "var(--muted-foreground)", letterSpacing: "0.06em" }}
      >
        {getRoleLabel(turn.type, t)}
      </div>
      <div className="min-w-0 overflow-hidden text-[12px] leading-[1.55]" style={{ color: "var(--subtle-foreground)" }}>
        {blocks.map((subBlock, index) => (
          <ContentBlockRenderer key={subBlock.id ?? index} block={subBlock} index={index} />
        ))}
      </div>
    </div>
  );
}
