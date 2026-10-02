// PROTOTYPE — #2972 各变体共用的小件：截断提示、详情栏三段结构、常驻保存栏、离开拦截。
// 这些是三套预设都已经定下的部分（来自「保存方式」与「页面容器档位」结论），不参与变体比较。

import { useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAppStore } from "@/stores/app-store";

/** 截断文本：溢出时悬停与键盘聚焦都能看到全文。 */
export function Trunc({ text, className, mono }: { text: string; className?: string; mono?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(false);
  const check = () => {
    const el = ref.current;
    if (el) setOverflow(el.scrollWidth > el.clientWidth + 1);
  };
  return (
    <Tooltip open={overflow ? undefined : false}>
      <TooltipTrigger
        render={
          <span
            ref={ref}
            onPointerEnter={check}
            onFocus={check}
            className={cn("block min-w-0 truncate", mono && "font-mono", className)}
          />
        }
      >
        {text}
      </TooltipTrigger>
      <TooltipContent className="max-w-md break-all">{text}</TooltipContent>
    </Tooltip>
  );
}

export function SampleTag() {
  return (
    <span className="shrink-0 rounded-sm border border-dashed border-border px-1 text-[11px] leading-4 text-text-3">样例</span>
  );
}

/** 详情栏：Header / Body / Footer 三段，Body 是这一栏唯一的滚动容器。 */
export function DetailPane({
  header,
  footer,
  children,
  bodyClassName,
  aside,
}: {
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  bodyClassName?: string;
  /** 检查器栏：与主体并排、各自滚动，保存栏横跨两者。 */
  aside?: ReactNode;
}) {
  return (
    <div data-zone="detail" className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {header && <div className="shrink-0 border-b border-border px-6 py-4">{header}</div>}
      <div className="flex min-h-0 flex-1">
        <div data-scroll-owner className={cn("relative min-h-0 min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]", bodyClassName)}>
          {children}
        </div>
        {aside}
      </div>
      {footer && <div className="shrink-0 border-t border-border bg-card px-6 py-3">{footer}</div>}
    </div>
  );
}

/** 常驻保存栏：干净时置灰，不隐藏。 */
export function SaveBar({
  dirty,
  onSave,
  onDiscard,
  extra,
  note,
  saveLabel = "保存",
  scope,
}: {
  dirty: boolean;
  onSave: () => void;
  onDiscard: () => void;
  extra?: ReactNode;
  note?: ReactNode;
  saveLabel?: string;
  scope?: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="min-w-0 flex-1 text-[13px]">
        {note ?? (
          <span className={dirty ? "text-warn" : "text-text-3"}>
            {dirty ? "有未保存的修改" : "所有修改已保存"}
            {scope && <span className="text-text-3">（{scope}）</span>}
          </span>
        )}
      </div>
      {extra}
      <Button variant="ghost" disabled={!dirty} onClick={onDiscard}>
        放弃修改
      </Button>
      <Button disabled={!dirty} onClick={onSave}>
        {saveLabel}
      </Button>
    </div>
  );
}

export function protoToast(text: string) {
  useAppStore.getState().pushToast(`原型：${text}（未写入后端）`, "success");
}

// ---------------------------------------------------------------------------
// 离开拦截：有草稿的编辑单元登记自己；主从切换、跳去别的分区都先过这里。
// ---------------------------------------------------------------------------

interface Guard {
  dirty: boolean;
  save: () => void;
  discard: () => void;
  label: string;
}
let guard: Guard | null = null;
let pending: (() => void) | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function registerGuard(g: Guard | null) {
  guard = g;
}

export function guarded(proceed: () => void) {
  if (guard?.dirty) {
    pending = proceed;
    emit();
  } else proceed();
}

export function LeaveDialog() {
  const open = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => pending !== null,
  );
  const close = () => {
    pending = null;
    emit();
  };
  const go = (action: "save" | "discard") => {
    const p = pending;
    if (action === "save") guard?.save();
    else guard?.discard();
    close();
    p?.();
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>离开前保存修改？</DialogTitle>
          <DialogDescription>「{guard?.label}」有未保存的修改。</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={close}>
            继续编辑
          </Button>
          <Button variant="outline" onClick={() => go("discard")}>
            放弃修改
          </Button>
          <Button onClick={() => go("save")}>保存并离开</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 标签列对齐的字段行：所有模型属性共用同一条标签列，控件起点一致。 */
export function FieldRow({ label, children, hint, labelFor }: { label: string; children: ReactNode; hint?: ReactNode; labelFor?: string }) {
  return (
    <div className="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-x-3">
      <label htmlFor={labelFor} className="pt-1.5 text-[13px] text-text-3">
        {label}
      </label>
      <div className="min-w-0">
        {children}
        {hint && <p className="mt-1 text-[12px] text-text-3">{hint}</p>}
      </div>
    </div>
  );
}

export const CONTROL = "h-8 rounded-md border border-input bg-input/30 px-2.5 text-[13px] text-text outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50";

export function StatusDot({ tone, label }: { tone: "good" | "muted" | "warn"; label: string }) {
  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full",
        tone === "good" ? "bg-good" : tone === "warn" ? "bg-warn" : "bg-text-3/60",
      )}
    />
  );
}

export function useWidth<T extends HTMLElement>() {
  const [el, setEl] = useState<T | null>(null);
  const width = useSyncExternalStore(
    (l) => {
      if (!el) return () => {};
      const ro = new ResizeObserver(l);
      ro.observe(el);
      return () => ro.disconnect();
    },
    () => (el ? Math.round(el.getBoundingClientRect().width) : 0),
  );
  return [setEl, width] as const;
}
