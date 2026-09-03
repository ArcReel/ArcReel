// PROTOTYPE — wayfinder #2310 共享零件：kicker、类型标签、Agent 状态行、清空确认、原型模拟控制条。评审后整目录删除。
import { useEffect, useState, type ReactNode } from "react";
import { Bot, FlaskConical } from "lucide-react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

import {
  INDEX_FILE,
  LEVEL_COPY,
  TYPE_LABELS,
  TYPE_TONE,
  memoryActions,
  relTime,
  type LevelState,
  type MemoryLevel,
  type MemoryType,
} from "./memory-prototype-store";

export function Kicker({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "muted" }) {
  return (
    <div className={"font-mono text-[10px] font-bold uppercase tracking-[0.16em] " + (tone === "accent" ? "text-accent-2" : "text-text-4")}>
      {children}
    </div>
  );
}

export function TypeBadge({ type, size = "sm" }: { type: MemoryType; size?: "sm" | "xs" }) {
  return (
    <span
      className={"inline-flex shrink-0 items-center rounded-[4px] font-mono font-bold uppercase tracking-[0.12em] " + (size === "xs" ? "px-1 py-px text-[9px]" : "px-1.5 py-0.5 text-[9.5px]")}
      style={{ background: `${TYPE_TONE[type]}26`, color: TYPE_TONE[type] }}
    >
      {TYPE_LABELS[type]}
    </span>
  );
}

/** 每 10 秒重渲一次，让「x 分钟前」自己走。 */
export function useTick() {
  const [, set] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => set((n) => n + 1), 10_000);
    return () => window.clearInterval(id);
  }, []);
}

export function AgentStatusLine({ state }: { state: LevelState }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-text-4">
      <Bot className="h-3 w-3" />
      {state.agentWriting ? (
        <span className="flex items-center gap-1.5 text-accent-2">
          <span aria-hidden className="h-[6px] w-[6px] animate-breathe rounded-full bg-accent" style={{ boxShadow: "0 0 0 3px oklch(0.7 0.15 295 / 0.25)" }} />
          Agent 正在写入记忆…
        </span>
      ) : state.lastAgentWriteAt ? (
        <span>Agent 上次写入 {relTime(state.lastAgentWriteAt)}</span>
      ) : (
        <span>Agent 尚未写入</span>
      )}
    </div>
  );
}

export function ClearMemoryDialog({ open, level, count, onCancel, onDone }: { open: boolean; level: MemoryLevel; count: number; onCancel: () => void; onDone: () => void }) {
  return (
    <ConfirmDialog
      open={open}
      tone="danger"
      title={`清空${LEVEL_COPY[level].short}的记忆？`}
      description={
        <span>
          将删除 <b className="text-text">{count}</b> 个文件（含索引）。Agent 会从零开始重新积累；正在进行的会话下次写入时会重建目录。此操作不可撤销。
        </span>
      }
      confirmLabel="清空记忆"
      onConfirm={() => {
        memoryActions.clear(level);
        onDone();
      }}
      onCancel={onCancel}
    />
  );
}

/** 原型专用：模拟 Agent 行为的控制条，不是设计的一部分。 */
export function ProtoControls({ level, currentFile }: { level: MemoryLevel; currentFile?: string | null }) {
  const btn = "rounded-[5px] border border-dashed border-warm/50 px-2 py-0.5 text-[10.5px] text-warm transition-colors hover:bg-warm/10";
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-[8px] border border-dashed border-warm/40 bg-warm/5 px-2.5 py-1.5">
      <span className="mr-1 inline-flex items-center gap-1 font-mono text-[9.5px] font-bold uppercase tracking-[0.14em] text-warm">
        <FlaskConical className="h-3 w-3" /> 原型模拟
      </span>
      <button type="button" className={btn} onClick={() => memoryActions.simulateAgentNewMemory(level)}>
        Agent 记一条新的（3s）
      </button>
      <button type="button" className={btn} onClick={() => memoryActions.simulateAgentTouch(level, INDEX_FILE)}>
        Agent 改索引
      </button>
      {currentFile && currentFile !== INDEX_FILE && (
        <button type="button" className={btn} onClick={() => memoryActions.simulateAgentTouch(level, currentFile)}>
          Agent 改当前文件
        </button>
      )}
      <button type="button" className={btn} onClick={() => memoryActions.clear(level)}>
        置空
      </button>
      <button type="button" className={btn} onClick={() => memoryActions.reseed(level)}>
        重置样例
      </button>
    </div>
  );
}

export function GhostButton({ children, onClick, disabled, danger, className = "" }: { children: ReactNode; onClick?: () => void; disabled?: boolean; danger?: boolean; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={GHOST_BTN_CLS + (danger ? " hover:border-danger/50 hover:text-danger-2" : "") + " " + className}
    >
      {children}
    </button>
  );
}
