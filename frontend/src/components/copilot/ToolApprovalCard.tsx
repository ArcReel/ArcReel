import React, { useEffect } from "react";
import type { PendingApproval } from "@/types";
import { cn } from "./chat/utils";

interface ToolApprovalCardProps {
  pendingApproval: PendingApproval;
  decidingApproval: boolean;
  onDecide: (requestId: string, decision: "allow" | "deny", updatedInput?: Record<string, unknown>, message?: string) => void;
}

export function ToolApprovalCard({
  pendingApproval,
  decidingApproval,
  onDecide,
}: ToolApprovalCardProps) {
  const handleAllow = () => {
    onDecide(pendingApproval.request_id, "allow");
  };

  const handleDeny = () => {
    onDecide(pendingApproval.request_id, "deny", undefined, "User denied tool execution.");
  };

  // Keyboard shortcuts: Esc = deny, Ctrl+Enter / Cmd+Enter = allow
  useEffect(() => {
    if (decidingApproval) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleDeny();
      } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        handleAllow();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- request_id and decidingApproval are the key deps
  }, [pendingApproval.request_id, decidingApproval]);

  const getToolColorClass = (toolName: string) => {
    switch (toolName.toLowerCase()) {
      case "bash":
      case "runcommand":
        return "text-red-400 border-red-500/20 bg-red-500/10";
      case "write":
      case "writetofile":
      case "edit":
      case "replacefilecontent":
      case "multiedit":
      case "multireplacefilecontent":
        return "text-amber-400 border-amber-500/20 bg-amber-500/10";
      default:
        return "text-blue-400 border-blue-500/20 bg-blue-500/10";
    }
  };

  const inputJson = JSON.stringify(pendingApproval.input, null, 2);

  return (
    <div className="border-t border-blue-400/20 bg-gradient-to-b from-blue-500/10 gap-3 to-transparent px-3 py-3">
      <div className="flex max-h-[min(34rem,52vh)] min-h-0 flex-col gap-3 rounded-xl border border-blue-400/20 bg-gray-950/60 p-3 shadow-sm">
        <div className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.18em] text-blue-400">
          工具执行审批 (Tool Approval)
        </div>

        <p className="text-sm text-slate-200">
          AI 智能体请求执行高危工具，请您确认是否允许该操作。
        </p>

        <section className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-white/10 bg-white/[0.03] p-3 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">工具名称:</span>
            <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-mono", getToolColorClass(pendingApproval.tool_name))}>
              {pendingApproval.tool_name}
            </span>
          </div>

          <div className="space-y-1 rounded-md bg-black/40 p-2">
            <span className="text-xs text-slate-400">输入参数 (Input):</span>
            <pre className="overflow-x-auto text-[11px] text-slate-300 font-mono">
              {inputJson}
            </pre>
          </div>
        </section>

        <div className="shrink-0 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={handleDeny}
            disabled={decidingApproval}
            title="Deny (Esc)"
            className="flex-1 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm font-medium text-red-400 transition-colors hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span>{decidingApproval ? "处理中..." : "拒绝 (Deny)"}</span>
            {!decidingApproval && (
              <span className="ml-1.5 text-[10px] opacity-50">Esc</span>
            )}
          </button>

          <button
            type="button"
            onClick={handleAllow}
            disabled={decidingApproval}
            title="Allow (Ctrl+Enter)"
            className="flex-1 rounded-lg bg-blue-500/80 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span>{decidingApproval ? "处理中..." : "允许 (Allow)"}</span>
            {!decidingApproval && (
              <span className="ml-1.5 text-[10px] opacity-60">⌃↵</span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
