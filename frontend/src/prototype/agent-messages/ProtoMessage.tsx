// PROTOTYPE — 消息区原型（#2980）：一轮消息的新形态（chat / transcript），以及待办清单、失败、压缩续接标记。
// 「现状」形态仍走 MessageRow；编辑态也回落到 MessageRow 的原地编辑器（本票不改编辑器）。

import { useState } from "react";
import { Check, Circle, Copy, Pencil, RotateCcw, Settings, TriangleAlert, Unplug } from "lucide-react";
import { Link } from "wouter";
import { cn } from "cn";
import type { ContentBlock, FailureObservation, TodoItem, Turn } from "@/types";
import { Message, MessageContent, MessageFooter } from "@/components/ui/message";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/CopyButton";
import { TextBlock } from "@/components/copilot/chat/TextBlock";
import { AgentFailureCard } from "@/components/copilot/chat/AgentFailureCard";
import { turnPlainText } from "@/components/copilot/chat/utils";
import { formatClockTime } from "@/utils/date-format";
import { copyText } from "@/utils/clipboard";
import type { AxisState } from "./axes";
import { BreathDot, WorkGroup, WorkRow, isWorkBlock } from "./ProtoBlocks";

const COMPACTION_PREFIX = "This session is being continued from a previous conversation";

/**
 * 显示用的「一轮」：两条用户消息之间连续的 assistant turn 合成一条（投影按 SDK 消息切分，
 * 一次回复常被拆成十几条），工序组与复制按钮都以合并后的整轮为单位。
 */
export function mergeAssistantRuns(turns: Turn[], draftTurn: Turn | null): Array<{ turn: Turn; streaming: boolean; key: string }> {
  const out: Array<{ turn: Turn; streaming: boolean; key: string }> = [];
  turns.forEach((t, i) => {
    const streaming = t === draftTurn;
    const last = out[out.length - 1];
    if (t.type === "assistant" && last?.turn.type === "assistant") {
      last.turn = { ...last.turn, content: [...last.turn.content, ...(t.content ?? [])], timestamp: t.timestamp ?? last.turn.timestamp };
      last.streaming = last.streaming || streaming;
      return;
    }
    out.push({ turn: t, streaming, key: streaming ? "draft" : t.uuid || `turn-${i}` });
  });
  return out;
}

/** 这条 turn 在新形态下是否有可见内容。只含空思考块（只有签名）或孤立 tool_result 的 turn 不占列表项。 */
export function hasVisibleContent(turn: Turn, streaming: boolean): boolean {
  if (streaming) return true;
  return (turn.content ?? []).some((b) => {
    if (b.type === "thinking") return Boolean(b.thinking?.trim());
    if (b.type === "text") return Boolean(b.text?.trim());
    if (b.type === "tool_result") return false;
    return true;
  });
}

interface Props {
  turn: Turn;
  axes: AxisState;
  streaming?: boolean;
  editable?: boolean;
  latestTodoId?: string;
  onStartEdit?: (uuid: string) => void;
}

type Segment = { kind: "prose"; block: ContentBlock } | { kind: "work"; blocks: ContentBlock[] } | { kind: "todo"; block: ContentBlock };

function segment(blocks: ContentBlock[], latestTodoId: string | undefined, inlineTodo: boolean): Segment[] {
  const out: Segment[] = [];
  for (const b of blocks) {
    if (inlineTodo && b.type === "tool_use" && b.name === "TodoWrite" && b.id === latestTodoId) {
      out.push({ kind: "todo", block: b });
    } else if (isWorkBlock(b)) {
      const last = out[out.length - 1];
      if (last?.kind === "work") last.blocks.push(b);
      else out.push({ kind: "work", blocks: [b] });
    } else {
      out.push({ kind: "prose", block: b });
    }
  }
  return out;
}

export function ProtoMessage({ turn, axes, streaming, editable, latestTodoId, onStartEdit }: Props) {
  const blocks = turn.content ?? [];
  if (blocks.length === 0) return null;
  const text = turnPlainText(turn);
  const time = formatClockTime(turn.timestamp);

  // 系统事件：中断、故障、后台任务通知
  if (turn.type === "system") {
    return (
      <div className="flex flex-col gap-1">
        {blocks.map((b, i) => {
          if (b.type === "interrupt_notice") {
            return (
              <Marker key={i} variant="separator" className="text-[12px]">
                <MarkerContent>已停止，这一轮的回复没有完成</MarkerContent>
              </Marker>
            );
          }
          if (b.type === "agent_failure" && b.failure) {
            return axes.failure === "compact" ? <FailureCompact key={i} failure={b.failure} /> : <AgentFailureCard key={i} failure={b.failure} />;
          }
          if (isWorkBlock(b)) return <WorkRow key={i} block={b} axes={axes} />;
          return null;
        })}
      </div>
    );
  }

  // 用户侧
  if (turn.type === "user") {
    if (text.startsWith(COMPACTION_PREFIX)) return <CompactionMarker text={text} />;
    const qa = blocks.find((b) => b.type === "question_answer");
    const images = blocks.filter((b) => b.type === "image" && b.source?.data);
    const body = (
      <>
        {images.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {images.map((b, i) => (
              <img
                key={i}
                src={`data:${b.source!.media_type};base64,${b.source!.data}`}
                alt={`图片附件 ${i + 1}`}
                className="h-20 max-w-full rounded-md border border-border object-cover"
              />
            ))}
          </div>
        )}
        {qa ? <AnswerList answers={qa.answers} fallback={qa.text} /> : text && <p className="text-[13px] leading-[1.6] whitespace-pre-wrap">{text}</p>}
      </>
    );
    const actions = !qa && (
      <UserActions text={text} time={time} canEdit={Boolean(editable && turn.uuid)} onEdit={() => turn.uuid && onStartEdit?.(turn.uuid)} />
    );

    if (axes.layout === "transcript") {
      return (
        <div className="group/msg flex flex-col gap-1">
          <div className="flex flex-col gap-2 border-l-2 border-primary/50 py-0.5 pl-3 text-foreground">{body}</div>
          {actions}
        </div>
      );
    }
    return (
      <Message align="end" className="group/msg">
        <MessageContent>
          <Bubble variant="tinted" align="end" className="max-w-[85%]">
            <BubbleContent className="flex flex-col gap-2 text-[13px]">{body}</BubbleContent>
          </Bubble>
          {actions}
        </MessageContent>
      </Message>
    );
  }

  // Agent 侧：正文无气泡，工序成行或成组
  const segs = segment(blocks, latestTodoId, axes.todo === "inline");
  return (
    <Message align="start" className="group/msg">
      <MessageContent className="gap-1.5">
        {segs.map((seg, i) => {
          const isLast = i === segs.length - 1;
          if (seg.kind === "todo") return <TodoChecklist key={i} block={seg.block} />;
          if (seg.kind === "work") {
            if (axes.work === "grouped") return <WorkGroup key={i} blocks={seg.blocks} axes={axes} live={Boolean(streaming && isLast)} />;
            return (
              <div key={i} className="flex flex-col">
                {seg.blocks.map((b, j) => (
                  <WorkRow key={b.id ?? j} block={b} axes={axes} streaming={Boolean(streaming && isLast && j === seg.blocks.length - 1)} />
                ))}
              </div>
            );
          }
          const b = seg.block;
          if (b.type === "text" && b.text) {
            return (
              <div key={i} className="max-w-[40em] text-[13px] leading-[1.65] text-foreground">
                <TextBlock text={b.text} />
              </div>
            );
          }
          if (b.type === "agent_failure" && b.failure) return <FailureCompact key={i} failure={b.failure} />;
          return null;
        })}
        {!streaming && text.trim() && (
          <MessageFooter className="h-6 gap-0.5 px-0 opacity-0 transition-opacity duration-150 group-hover/msg:opacity-100 group-focus-within/msg:opacity-100">
            <CopyButton text={text} />
            {time && <span className="ml-1 text-[11px] font-normal tabular-nums">{time}</span>}
          </MessageFooter>
        )}
      </MessageContent>
    </Message>
  );
}

function UserActions({ text, time, canEdit, onEdit }: { text: string; time: string | null; canEdit: boolean; onEdit: () => void }) {
  return (
    <MessageFooter className="h-6 gap-0.5 px-0 opacity-0 transition-opacity duration-150 group-hover/msg:opacity-100 group-focus-within/msg:opacity-100">
      {time && <span className="mr-1 text-[11px] font-normal tabular-nums">{time}</span>}
      {text.trim() && <CopyButton text={text} />}
      {canEdit && (
        <Button variant="ghost" size="icon-xs" onClick={onEdit} aria-label="编辑并重新发送" title="编辑并重新发送">
          <Pencil />
        </Button>
      )}
    </MessageFooter>
  );
}

function AnswerList({ answers, fallback }: { answers?: Record<string, string>; fallback?: string }) {
  if (!answers || Object.keys(answers).length === 0) return <p className="text-[13px]">{fallback}</p>;
  return (
    <dl className="flex flex-col gap-1.5 text-[12.5px]">
      {Object.entries(answers).map(([q, a]) => (
        <div key={q} className="min-w-0">
          <dt className="text-muted-foreground">{q}</dt>
          <dd className="font-medium text-foreground">{a}</dd>
        </div>
      ))}
    </dl>
  );
}

function CompactionMarker({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <Marker variant="separator" className="text-[12px]">
        <MarkerContent>
          <button type="button" className="hover:text-foreground" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            上下文已压缩，以下接着之前的对话 · {open ? "收起摘要" : "查看摘要"}
          </button>
        </MarkerContent>
      </Marker>
      {open && <div className="max-w-[40em] rounded-lg bg-muted px-3 py-2 text-[12px] leading-[1.6] whitespace-pre-wrap text-text-2">{text}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 待办清单（随消息流）
// ---------------------------------------------------------------------------

export function TodoChecklist({ block }: { block: ContentBlock }) {
  const todos = (Array.isArray(block.input?.todos) ? block.input.todos : []) as TodoItem[];
  const done = todos.filter((t) => t.status === "completed").length;
  return (
    <div className="max-w-[40em] rounded-lg border border-border bg-card px-3 py-2">
      <div className="mb-1.5 flex items-center justify-between text-[12px]">
        <span className="text-text-2">待办</span>
        <span className="tabular-nums text-muted-foreground">
          {done}/{todos.length}
        </span>
      </div>
      <TodoList todos={todos} />
    </div>
  );
}

export function TodoList({ todos }: { todos: TodoItem[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {todos.map((t, i) => (
        <li key={i} className="flex items-start gap-2 text-[12.5px] leading-[1.5]">
          <span className="mt-[3px] grid size-3.5 shrink-0 place-items-center">
            {t.status === "completed" ? (
              <Check className="size-3.5 text-muted-foreground" />
            ) : t.status === "in_progress" ? (
              <BreathDot />
            ) : (
              <Circle className="size-3 text-muted-foreground" />
            )}
          </span>
          <span className={cn(t.status === "completed" ? "text-muted-foreground line-through" : t.status === "in_progress" ? "text-foreground" : "text-text-2")}>
            {t.status === "in_progress" ? t.activeForm : t.content}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// 失败（紧凑）：一句话结论 + 操作，原始信息折叠
// ---------------------------------------------------------------------------

function failureSentence(f: FailureObservation): string {
  const type = String(f.summary.type ?? "");
  const status = f.summary.status != null ? `（${String(f.summary.status)}）` : "";
  if (type.includes("overloaded")) return `模型服务暂时过载${status}，稍后重试通常就能恢复。`;
  if (type.includes("auth") || f.summary.status === 401) return `模型服务拒绝了访问密钥${status}，请检查 Agent 供应商的密钥。`;
  if (type.includes("rate")) return `请求太频繁被限流${status}，稍等片刻再试。`;
  return `模型服务返回错误${status}。`;
}

export function FailureCompact({ failure, onRetry }: { failure: FailureObservation; onRetry?: () => void }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const startup = failure.phase === "startup";
  return (
    <div role="alert" className="max-w-[40em] rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5">
      <div className="flex items-start gap-2">
        {startup ? <Unplug className="mt-0.5 size-4 shrink-0 text-destructive" /> : <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />}
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium text-foreground">{startup ? "Agent 没能启动" : "这一轮没有完成"}</p>
          <p className="mt-0.5 text-[12.5px] leading-[1.55] text-text-2">{failureSentence(failure)}</p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {onRetry && (
          <Button size="xs" variant="outline" onClick={onRetry}>
            <RotateCcw data-icon="inline-start" />
            重试
          </Button>
        )}
        <Button size="xs" variant="ghost" render={<Link href="/app/settings?section=agent" />}>
          <Settings data-icon="inline-start" />
          Agent 设置
        </Button>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => void copyText(JSON.stringify(failure, null, 2)).then(() => setCopied(true))}
        >
          {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
          {copied ? "已复制" : "复制诊断信息"}
        </Button>
        <button type="button" className="ml-auto text-[11.5px] text-muted-foreground hover:text-foreground" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "收起详情" : "详情"}
        </button>
      </div>
      {open && (
        <pre className="mt-2 font-mono text-[11px] leading-[1.5] whitespace-pre-wrap break-all text-text-2">
          {String(failure.summary.message ?? "")}
          {"\n\n"}
          {JSON.stringify(failure.raw, null, 2)}
        </pre>
      )}
    </div>
  );
}
