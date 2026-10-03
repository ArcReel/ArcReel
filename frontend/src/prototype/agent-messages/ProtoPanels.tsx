// PROTOTYPE — 消息区原型（#2980）：面板级部件——待办单行进度、提问 Questionnaire、会话切换器 / 历史视图、输入框内附件。
// 原型写法：删除会话只弹确认、不真删；会话不足三条时补样本会话撑密度；文案直接写中文。

import { useMemo, useState, type FormEvent } from "react";
import { Check, ChevronDown, ChevronUp, History, ImageIcon, MapPin, MessageSquare, Plus, Puzzle, Trash2, User, X, Film } from "lucide-react";
import { cn } from "cn";
import type { PendingQuestion, SessionMeta, TodoItem, Turn } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAppStore } from "@/stores/app-store";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment";
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSubmit,
  QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { extractLatestTodos } from "@/components/copilot/TodoListPanel";
import { formatShortDateTime } from "@/utils/date-format";
import { BreathDot } from "./ProtoBlocks";
import { TodoList } from "./ProtoMessage";

// ---------------------------------------------------------------------------
// 待办：输入框上方单行进度，点开看清单
// ---------------------------------------------------------------------------

export function TodoDockCompact({ turns, draftTurn }: { turns: Turn[]; draftTurn: Turn | null }) {
  const todos = useMemo(() => extractLatestTodos(turns, draftTurn), [turns, draftTurn]);
  const [open, setOpen] = useState(false);
  if (!todos || todos.length === 0 || todos.every((t) => t.status === "completed")) return null;
  const done = todos.filter((t) => t.status === "completed").length;
  const current = todos.find((t) => t.status === "in_progress");
  return (
    <div className="border-t border-border px-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-full min-w-0 items-center gap-2 text-left text-[12px] focus-visible:outline-2 focus-visible:outline-ring"
      >
        {current ? <BreathDot /> : <Check className="size-3.5 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate text-text-2">{current?.activeForm ?? "待办"}</span>
        <span className="tabular-nums text-muted-foreground">
          {done}/{todos.length}
        </span>
        {open ? <ChevronDown className="size-3.5 text-muted-foreground" /> : <ChevronUp className="size-3.5 text-muted-foreground" />}
      </button>
      {open && (
        <div className="pb-2">
          <TodoList todos={todos} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 提问：Questionnaire
// ---------------------------------------------------------------------------

export function ProtoQuestionnaire({
  pending,
  busy,
  error,
  onSubmit,
  placement,
}: {
  pending: PendingQuestion;
  busy: boolean;
  error: string | null;
  onSubmit: (answers: Record<string, string>) => void;
  placement: "composer" | "inline";
}) {
  const items = pending.questions.map((q, i) => ({
    name: `q${i}`,
    required: true,
    choices: q.options.map((o) => ({ value: o.label })),
  }));

  const handle = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const answers: Record<string, string> = {};
    pending.questions.forEach((q, i) => {
      const vals = fd
        .getAll(`q${i}`)
        .map(String)
        .filter((v) => v.trim());
      answers[q.question] = vals.join("、");
    });
    onSubmit(answers);
  };

  return (
    <Questionnaire
      key={pending.question_id}
      items={items}
      shortcuts="numbers"
      onSubmit={handle}
      className={cn("gap-3", placement === "composer" ? "flex min-h-0 flex-1 flex-col" : "rounded-lg border border-border bg-card p-3")}
    >
      <div className="flex shrink-0 items-center gap-2 text-[12px] text-muted-foreground">
        <BreathDot />
        <span className="flex-1 text-text-2">Agent 在等你回答</span>
        <QuestionnaireProgress
          className="min-w-0 text-[11px]"
          render={(props, state) => (
            <div {...props}>
              第 {state.current + 1} / {state.total} 题
            </div>
          )}
        />
      </div>
      <div className={cn(placement === "composer" && "min-h-0 flex-1 overflow-y-auto overscroll-contain")}>
        {pending.questions.map((q, i) => (
          <QuestionnaireItem key={i} name={`q${i}`} multiple={q.multiSelect} required className="gap-3">
            <QuestionnaireTitle className="text-[13.5px] [&:not(:has(~[data-slot=questionnaire-description]))]:mb-0">
              {q.header && <span className="mr-1.5 text-muted-foreground">{q.header} ·</span>}
              {q.question}
              {q.multiSelect && <span className="ml-1.5 text-[12px] font-normal text-muted-foreground">可多选</span>}
            </QuestionnaireTitle>
            <QuestionnaireChoices>
              {q.options.map((o) => (
                <QuestionnaireChoice key={o.label} value={o.label} className="min-h-0 py-2 text-[13px]">
                  <span className="font-medium">{o.label}</span>
                  {o.description && <QuestionnaireChoiceDescription className="text-[12px]">{o.description}</QuestionnaireChoiceDescription>}
                </QuestionnaireChoice>
              ))}
              <QuestionnaireInput aria-label="其他回答" placeholder="都不合适？直接写下你的想法" className="text-[13px] md:text-[13px]" />
            </QuestionnaireChoices>
          </QuestionnaireItem>
        ))}
      </div>
      {error && <p className="shrink-0 text-[12px] text-destructive">{error}</p>}
      <QuestionnaireActions className="shrink-0">
        <QuestionnairePrevious size="sm" variant="ghost">
          上一题
        </QuestionnairePrevious>
        <QuestionnaireNext size="sm">下一题</QuestionnaireNext>
        <QuestionnaireSubmit size="sm" disabled={busy}>
          {busy ? "提交中…" : "提交回答"}
        </QuestionnaireSubmit>
      </QuestionnaireActions>
    </Questionnaire>
  );
}

// ---------------------------------------------------------------------------
// 会话：样本补位 + 切换器 + 历史视图
// ---------------------------------------------------------------------------

const SAMPLE_SESSIONS: SessionMeta[] = [
  ["第 7 集分镜返工：滑膛特写换参考图", "completed", 1],
  ["导入原著第 6–9 章并拆分集", "completed", 2],
  ["给朱汉杨补三套服装衍生", "error", 3],
  ["配音：旁白音色从「沉稳」改成「冷静」", "completed", 5],
  ["第 5 集剪辑：U03 与 U04 交换顺序", "interrupted", 8],
  ["风格模版从「电影感」换成「冷调写实」", "completed", 12],
].map(([title, status, days], i) => ({
  id: `proto-sample-${i}`,
  project_name: "proj-b961d00c",
  title: title as string,
  status: status as SessionMeta["status"],
  created_at: new Date(Date.now() - (days as number) * 86400000).toISOString(),
  updated_at: new Date(Date.now() - (days as number) * 86400000).toISOString(),
}));

function useSessionList(): SessionMeta[] {
  const sessions = useAssistantStore((s) => s.sessions);
  return sessions.length >= 3 ? sessions : [...sessions, ...SAMPLE_SESSIONS];
}

function relTime(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return formatShortDateTime(iso) ?? "今天";
  if (days === 1) return "昨天";
  return `${days} 天前`;
}

function SessionDot({ status }: { status: string }) {
  if (status === "running") return <BreathDot />;
  return <span aria-hidden className={cn("inline-block size-1.5 shrink-0 rounded-full", status === "error" ? "bg-destructive" : "bg-muted-foreground/40")} />;
}

function useCurrentTitle() {
  const { sessions, currentSessionId, isDraftSession } = useAssistantStore();
  if (isDraftSession || !currentSessionId) return "新会话";
  const s = sessions.find((x) => x.id === currentSessionId);
  return s?.title || formatShortDateTime(s?.created_at) || "新会话";
}

function DeleteConfirm({ target, onClose }: { target: SessionMeta | null; onClose: () => void }) {
  return (
    <AlertDialog open={Boolean(target)} onOpenChange={(o) => !o && onClose()}>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>删除这个会话？</AlertDialogTitle>
          <AlertDialogDescription>「{target?.title}」的对话记录会被删除，无法恢复。已生成的分镜、视频等产物不受影响。（原型：不会真删）</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onClose}>
            删除会话
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SessionCommandList({ onPick, onDelete }: { onPick: (id: string) => void; onDelete: (s: SessionMeta) => void }) {
  const list = useSessionList();
  const currentId = useAssistantStore((s) => s.currentSessionId);
  return (
    <Command className="bg-transparent">
      <CommandInput placeholder="搜索会话" />
      <CommandList className="max-h-[min(360px,50dvh)]">
        <CommandEmpty>没有匹配的会话</CommandEmpty>
        <CommandGroup>
          {list.map((s) => (
            <CommandItem key={s.id} value={`${s.title} ${s.id}`} onSelect={() => onPick(s.id)} className="group/item gap-2 text-[13px]">
              <SessionDot status={s.status} />
              <span className="min-w-0 flex-1 truncate">{s.title || "未命名会话"}</span>
              <span className="shrink-0 text-[11px] text-muted-foreground group-hover/item:hidden">{relTime(s.updated_at)}</span>
              {s.id === currentId && <Check className="size-3.5 shrink-0" />}
              <button
                type="button"
                aria-label={`删除会话「${s.title}」`}
                className="hidden size-6 shrink-0 place-items-center rounded-md text-muted-foreground group-hover/item:grid hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s);
                }}
              >
                <Trash2 className="size-3.5" />
              </button>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </Command>
  );
}

export function SessionSwitcher({ onSwitch, onNew }: { onSwitch: (id: string) => void; onNew: () => void }) {
  const [open, setOpen] = useState(false);
  const [toDelete, setToDelete] = useState<SessionMeta | null>(null);
  const title = useCurrentTitle();
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <Button variant="ghost" size="sm" className="min-w-0 max-w-full justify-start gap-1 px-1.5 text-[13px] font-medium">
              <span className="truncate">{title}</span>
              <ChevronDown data-icon="inline-end" className="text-muted-foreground" />
            </Button>
          }
        />
        <PopoverContent align="start" className="w-[min(320px,calc(100cqw-24px))] p-0">
          <SessionCommandList
            onPick={(id) => {
              setOpen(false);
              if (!id.startsWith("proto-")) onSwitch(id);
            }}
            onDelete={(s) => {
              setOpen(false);
              setToDelete(s);
            }}
          />
          <div className="border-t border-border p-1">
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              onClick={() => {
                setOpen(false);
                onNew();
              }}
            >
              <Plus data-icon="inline-start" />
              新建会话
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      <DeleteConfirm target={toDelete} onClose={() => setToDelete(null)} />
    </>
  );
}

export function HistoryTitle() {
  const title = useCurrentTitle();
  return <span className="min-w-0 truncate px-1.5 text-[13px] font-medium">{title}</span>;
}

export function HistoryToggle({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  return (
    <Button variant={active ? "secondary" : "ghost"} size="icon-sm" aria-pressed={active} aria-label="会话历史" title="会话历史" onClick={onToggle}>
      <History />
    </Button>
  );
}

export function HistoryView({ onPick }: { onPick: (id: string) => void }) {
  const [toDelete, setToDelete] = useState<SessionMeta | null>(null);
  return (
    <div className="flex min-h-0 flex-1 flex-col px-2 py-2">
      <div className="px-1.5 pb-1 text-[12px] text-muted-foreground">本项目的会话</div>
      <div className="min-h-0 flex-1 [&_[data-slot=command-list]]:max-h-none">
        <SessionCommandList onPick={onPick} onDelete={setToDelete} />
      </div>
      <DeleteConfirm target={toDelete} onClose={() => setToDelete(null)} />
    </div>
  );
}

export function NewSessionButton({ onNew }: { onNew: () => void }) {
  return (
    <Button variant="ghost" size="icon-sm" aria-label="新建会话" title="新建会话" onClick={onNew}>
      <Plus />
    </Button>
  );
}

export function SessionIcon() {
  return <MessageSquare className="size-3.5 text-muted-foreground" />;
}

// ---------------------------------------------------------------------------
// 输入框内附件：上下文 + 图片
// ---------------------------------------------------------------------------

const CTX_ICON = { character: User, scene: MapPin, prop: Puzzle, segment: Film } as const;
const CTX_LABEL = { character: "角色", scene: "场景", prop: "道具", segment: "分镜" } as const;

export function ComposerAttachments({
  images,
  onRemoveImage,
  onPreview,
}: {
  images: Array<{ id: string; dataUrl: string }>;
  onRemoveImage: (id: string) => void;
  onPreview: (src: string) => void;
}) {
  const ctx = useAppStore((s) => s.focusedContext);
  const setCtx = useAppStore((s) => s.setFocusedContext);
  if (!ctx && images.length === 0) return null;
  const Icon = ctx ? CTX_ICON[ctx.type] : null;
  return (
    <AttachmentGroup className="flex flex-wrap gap-1.5 pb-1.5">
      {ctx && Icon && (
        <Attachment size="xs" className="w-auto max-w-full">
          <AttachmentMedia>
            <Icon />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle className="text-[12px]">
              {CTX_LABEL[ctx.type]}：{ctx.id}
            </AttachmentTitle>
            <AttachmentDescription className="text-[11px]">随消息一起发给 Agent</AttachmentDescription>
          </AttachmentContent>
          <AttachmentActions>
            <AttachmentAction aria-label="移除上下文" onClick={() => setCtx(null)}>
              <X />
            </AttachmentAction>
          </AttachmentActions>
        </Attachment>
      )}
      {images.map((img, i) => (
        <Attachment key={img.id} size="xs" className="w-auto">
          <AttachmentMedia variant="image" className="cursor-zoom-in" onClick={() => onPreview(img.dataUrl)}>
            <img src={img.dataUrl} alt={`图片 ${i + 1}`} />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle className="text-[12px]">图片 {i + 1}</AttachmentTitle>
          </AttachmentContent>
          <AttachmentActions>
            <AttachmentAction aria-label={`移除图片 ${i + 1}`} onClick={() => onRemoveImage(img.id)}>
              <X />
            </AttachmentAction>
          </AttachmentActions>
        </Attachment>
      ))}
    </AttachmentGroup>
  );
}

export { ImageIcon };
