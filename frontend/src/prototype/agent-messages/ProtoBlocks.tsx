// PROTOTYPE — 消息区原型（#2980）：工序行（工具调用、子代理、Skill、任务进度、思考）的新形态。
// 「rows」= 每个工序一行；「grouped」= 一轮里连续的工序合并成一组。正文、待办、失败另见 ProtoMessage。
// 原型写法：显示名直接写中文，没有走 i18n。

import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Bot,
  Brain,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Eye,
  FilePen,
  FilePlus,
  FileText,
  Film,
  FolderSearch,
  Globe,
  ListChecks,
  ListTree,
  MessageCircleQuestion,
  Search,
  Sparkles,
  SquareTerminal,
  Wrench,
  Zap,
} from "lucide-react";
import { cn } from "cn";
import type { ContentBlock, TodoItem, Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { TERMINAL_SESSION_STATUSES } from "@/components/copilot/chat/utils";
import { TextBlock } from "@/components/copilot/chat/TextBlock";
import type { AxisState } from "./axes";

// ---------------------------------------------------------------------------
// 分类
// ---------------------------------------------------------------------------

export function isWorkBlock(block: ContentBlock): boolean {
  return (
    block.type === "tool_use" ||
    block.type === "thinking" ||
    block.type === "skill_invocation" ||
    block.type === "task_progress" ||
    block.type === "tool_result"
  );
}

type Status = "running" | "ok" | "error" | "stopped";

function useSessionDone() {
  const s = useAssistantStore((st) => st.sessionStatus);
  return s != null && TERMINAL_SESSION_STATUSES.has(s);
}

function blockStatus(block: ContentBlock, sessionDone: boolean): Status {
  if (block.type === "task_progress") {
    if (block.status === "task_notification") return block.task_status === "failed" ? "error" : "ok";
    return sessionDone ? "stopped" : "running";
  }
  if (block.type === "skill_invocation" || block.type === "thinking") return "ok";
  if (block.is_error || block.task_info?.task_status === "failed") return "error";
  if (block.result !== undefined || block.task_info?.task_status === "completed") return "ok";
  return sessionDone ? "stopped" : "running";
}

const BUILTIN: Record<string, [string, typeof Wrench]> = {
  Read: ["读取文件", FileText],
  Write: ["写入文件", FilePlus],
  Edit: ["修改文件", FilePen],
  Bash: ["运行命令", SquareTerminal],
  Grep: ["搜索内容", Search],
  Glob: ["查找文件", FolderSearch],
  WebSearch: ["网页搜索", Globe],
  WebFetch: ["读取网页", Globe],
  TodoWrite: ["更新待办", ListChecks],
  AskUserQuestion: ["提问", MessageCircleQuestion],
};

function mcpIcon(id: string) {
  if (id.startsWith("generate")) return Sparkles;
  if (id.startsWith("inspect") || id.startsWith("get")) return Eye;
  if (id.includes("draft")) return FilePen;
  if (id.includes("timeline")) return Film;
  return Wrench;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** 人能读的一句摘要：认得的参数翻成话，不认得的才退回截断 JSON。 */
function toolSummary(name: string, input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  if (name === "Read" || name === "Write" || name === "Edit") return str(input.file_path).split("/").slice(-2).join("/");
  if (name === "Bash") return str(input.command);
  if (name === "Grep") return `“${str(input.pattern)}”`;
  if (name === "Glob") return str(input.pattern);
  if (name === "WebSearch") return str(input.query);
  if (name === "WebFetch") return str(input.url);
  if (name === "TodoWrite") {
    const todos = Array.isArray(input.todos) ? (input.todos as TodoItem[]) : [];
    return `${todos.filter((t) => t.status === "completed").length}/${todos.length} 已完成`;
  }
  if (name === "AskUserQuestion") {
    const qs = Array.isArray(input.questions) ? (input.questions as Array<{ question?: string }>) : [];
    return qs.map((q) => q.question).filter(Boolean).join(" / ");
  }
  const parts: string[] = [];
  const target = (input.target ?? {}) as Record<string, unknown>;
  const ep = input.episode_id ?? input.episode ?? target.episode_id;
  if (ep != null) parts.push(`第 ${String(ep)} 集`);
  const ids = (input.unit_ids ?? target.ids);
  if (Array.isArray(ids)) parts.push(ids.map((i) => String(i).replace(/^E\d+/, "")).join("、"));
  if (typeof input.name === "string") parts.push(`「${input.name}」`);
  if (parts.length) return parts.join(" · ");
  const json = JSON.stringify(input);
  return json.length > 80 ? `${json.slice(0, 80)}…` : json;
}

function useWorkLabel(block: ContentBlock): { name: string; summary: string; Icon: typeof Wrench } {
  const { t } = useTranslation("dashboard");
  if (block.type === "thinking") return { name: "思考", summary: firstLine(block.thinking ?? ""), Icon: Brain };
  if (block.type === "skill_invocation") return { name: `/${block.skill_name ?? ""}`, summary: block.skill_args ?? "", Icon: Zap };
  if (block.type === "task_progress") {
    return { name: "后台任务", summary: block.summary || block.description || "", Icon: ListTree };
  }
  if (block.type === "tool_result") return { name: "工具结果", summary: "", Icon: Wrench };
  const name = block.name ?? "Tool";
  if (name === "Agent" || name === "Task" || block.sub_turns) {
    // 锚点缺描述时（如压缩续接后的子代理）用子时间线首条文本的首行兜底
    const firstSub = block.sub_turns?.[0]?.content?.find((b) => b.type === "text")?.text ?? "";
    const desc = str(block.input?.description) || block.task_info?.description || firstLine(firstSub).replace(/^任务类型：/, "");
    return { name: "子代理", summary: desc, Icon: Bot };
  }
  if (name === "Skill") return { name: `/${str(block.input?.skill)}`, summary: str(block.input?.args), Icon: Zap };
  const mcp = /^mcp__arcreel__([a-z0-9_]+)$/.exec(name);
  if (mcp) return { name: t(`tool_name_${mcp[1]}`, { defaultValue: mcp[1] }), summary: toolSummary(name, block.input), Icon: mcpIcon(mcp[1]) };
  const builtin = BUILTIN[name];
  return { name: builtin?.[0] ?? name, summary: toolSummary(name, block.input), Icon: builtin?.[1] ?? Wrench };
}

function firstLine(text: string): string {
  return text.split("\n").find((l) => l.trim())?.trim() ?? "";
}

// ---------------------------------------------------------------------------
// 运行中指示：呼吸点（只变透明度，见「视觉方向」结论）
// ---------------------------------------------------------------------------

export function BreathDot({ className }: { className?: string }) {
  return <span aria-hidden className={cn("proto-breath inline-block size-1.5 shrink-0 rounded-full bg-primary", className)} />;
}

function StatusMark({ status }: { status: Status }) {
  if (status === "running") return <BreathDot />;
  if (status === "error") return <CircleAlert aria-label="失败" className="size-3.5 shrink-0 text-destructive" />;
  if (status === "stopped") return <span className="shrink-0 text-[11px] text-muted-foreground">已停止</span>;
  return null;
}

// ---------------------------------------------------------------------------
// 单行工序
// ---------------------------------------------------------------------------

function Clamp({ text, error }: { text: string; error?: boolean }) {
  const [all, setAll] = useState(false);
  const long = text.split("\n").length > 10 || text.length > 900;
  return (
    <div>
      <pre
        className={cn(
          "font-mono text-[11.5px] leading-[1.55] whitespace-pre-wrap break-all",
          error ? "text-destructive" : "text-text-2",
          long && !all && "line-clamp-[10]",
        )}
      >
        {text}
      </pre>
      {long && (
        <button type="button" onClick={() => setAll((v) => !v)} className="mt-1 text-[11px] text-muted-foreground hover:text-foreground">
          {all ? "收起" : "显示全部"}
        </button>
      )}
    </div>
  );
}

function RowShell({
  icon,
  name,
  summary,
  status,
  children,
  defaultOpen = false,
}: {
  icon: ReactNode;
  name: string;
  summary: string;
  status: Status;
  children?: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const expandable = Boolean(children);
  const head = (
    <>
      <span className={cn("grid size-4 shrink-0 place-items-center", status === "error" ? "text-destructive" : "text-muted-foreground")}>{icon}</span>
      <span className={cn("shrink-0 text-[12px]", status === "error" ? "text-destructive" : "text-text-2")}>{name}</span>
      <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground">{summary}</span>
      <StatusMark status={status} />
      {expandable && (
        <ChevronRight
          aria-hidden
          className={cn("size-3.5 shrink-0 text-muted-foreground opacity-0 group-hover/row:opacity-100 group-focus-visible/row:opacity-100", open && "rotate-90 opacity-100")}
        />
      )}
    </>
  );
  return (
    <div className="min-w-0">
      {expandable ? (
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="group/row flex h-7 w-full min-w-0 items-center gap-2 rounded-md px-1.5 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
        >
          {head}
        </button>
      ) : (
        <div className="flex h-7 w-full min-w-0 items-center gap-2 px-1.5">{head}</div>
      )}
      {open && children && <div className="mt-0.5 mb-1.5 ml-[13px] flex flex-col gap-2 border-l border-border pl-3">{children}</div>}
    </div>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-0.5 text-[11px] text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

export function WorkRow({ block, axes, streaming }: { block: ContentBlock; axes: AxisState; streaming?: boolean }) {
  const sessionDone = useSessionDone();
  const { name, summary, Icon } = useWorkLabel(block);
  const status = streaming && block.type === "thinking" ? "running" : blockStatus(block, sessionDone);

  if (block.type === "thinking") {
    const text = block.thinking ?? "";
    if (streaming) {
      return (
        <div className="flex h-7 items-center gap-2 px-1.5 text-[12px] text-muted-foreground">
          <Brain className="size-3.5" />
          <span className="shimmer">正在思考</span>
        </div>
      );
    }
    if (!text.trim()) return null;
    return (
      <RowShell icon={<Brain className="size-3.5" />} name={axes.thinking === "label" ? "思考过程" : "思考"} summary={axes.thinking === "label" ? "" : firstLine(text)} status="ok">
        <p className="text-[12px] leading-[1.6] whitespace-pre-wrap text-muted-foreground">{text}</p>
      </RowShell>
    );
  }

  if (block.type === "tool_use" && (block.name === "Agent" || block.name === "Task" || block.sub_turns)) {
    const tokens = block.task_info?.usage?.total_tokens;
    const subTurns = block.sub_turns ?? [];
    const result = typeof block.result === "string" ? block.result : "";
    return (
      <RowShell
        icon={<Icon className="size-3.5" />}
        name={name}
        summary={[summary, status === "running" && tokens != null ? `${(tokens / 1000).toFixed(1)}k tokens` : ""].filter(Boolean).join(" · ")}
        status={status}
      >
        {subTurns.length > 0 && (
          <div className="flex flex-col">
            {subTurns.map((turn, i) => (
              <SubTurn key={turn.uuid ?? i} turn={turn} axes={axes} />
            ))}
          </div>
        )}
        {result && (
          <Section label="子代理结论">
            <div className="text-[12.5px]">
              <TextBlock text={result} />
            </div>
          </Section>
        )}
      </RowShell>
    );
  }

  const input = block.input && Object.keys(block.input).length > 0 ? JSON.stringify(block.input, null, 2) : "";
  const result = block.result === undefined ? "" : typeof block.result === "string" ? block.result : JSON.stringify(block.result, null, 2);
  const hasDetail = block.type === "tool_use" && (input || result);
  return (
    <RowShell icon={<Icon className="size-3.5" />} name={name} summary={summary} status={status}>
      {hasDetail ? (
        <>
          {input && (
            <Section label="参数">
              <Clamp text={input} />
            </Section>
          )}
          {result && (
            <Section label={block.is_error ? "错误" : "结果"}>
              <Clamp text={result} error={block.is_error} />
            </Section>
          )}
        </>
      ) : null}
    </RowShell>
  );
}

function SubTurn({ turn, axes }: { turn: Turn; axes: AxisState }) {
  return (
    <>
      {(turn.content ?? []).map((b, i) =>
        isWorkBlock(b) ? (
          <WorkRow key={b.id ?? i} block={b} axes={{ ...axes, work: "rows" }} />
        ) : b.type === "text" && b.text ? (
          <div key={i} className="px-1.5 py-1 text-[12px] text-text-2">
            <TextBlock text={b.text} />
          </div>
        ) : null,
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// 一组工序
// ---------------------------------------------------------------------------

export function WorkGroup({ blocks, axes, live }: { blocks: ContentBlock[]; axes: AxisState; live: boolean }) {
  const sessionDone = useSessionDone();
  const [open, setOpen] = useState(false);
  const statuses = blocks.map((b) => blockStatus(b, sessionDone));
  const failed = statuses.filter((s) => s === "error").length;
  const running = live || statuses.includes("running");
  const last = blocks[blocks.length - 1];
  const steps = blocks.filter((b) => b.type !== "thinking").length;

  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="group/row flex h-7 w-full min-w-0 items-center gap-2 rounded-md px-1.5 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
      >
        <span className="grid size-4 shrink-0 place-items-center text-muted-foreground">
          {running ? <BreathDot /> : failed ? <CircleAlert className="size-3.5 text-destructive" /> : <CircleCheck className="size-3.5" />}
        </span>
        {running ? (
          <LiveStep block={last} live={live} />
        ) : (
          <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground">
            {steps > 0 ? `执行了 ${steps} 个步骤` : "思考过程"}
            {failed > 0 && <span className="text-destructive">，{failed} 个失败</span>}
          </span>
        )}
        <ChevronRight aria-hidden className={cn("size-3.5 shrink-0 text-muted-foreground", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-0.5 mb-1.5 ml-[13px] flex flex-col border-l border-border pl-2">
          {blocks.map((b, i) => (
            <WorkRow key={b.id ?? i} block={b} axes={axes} streaming={live && i === blocks.length - 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function LiveStep({ block, live }: { block: ContentBlock; live: boolean }) {
  const { name, summary } = useWorkLabel(block);
  const label = block.type === "thinking" && live ? "正在思考" : `正在${name.startsWith("/") ? "运行 " : ""}${name}`;
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2 text-[12px]">
      <span className="shimmer shrink-0 text-text-2">{label}</span>
      {block.type !== "thinking" && <span className="min-w-0 truncate text-muted-foreground">{summary}</span>}
    </span>
  );
}
