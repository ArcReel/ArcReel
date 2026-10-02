// PROTOTYPE — 剧集页原型（#2974）：「制作进度」的页头入口。
// 收起时只是一枚胶囊（状态 + 一句话现状 + 阻断数），点开后用右侧抽屉或下拉面板展示完整步骤清单。
// 两种容器都自带唯一的滚动区，不再把画布挤到 0 高。下一步只在所属步骤行展开，顶部不再重复。

import { useState, type ReactNode } from "react";
import { ListChecks } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

interface Props {
  mode: "drawer" | "popover";
  title: string;
  headline: string;
  blockerCount: number;
  nextActs: ReactNode;
  refreshFailed: string | null;
  children: ReactNode;
}

export function ProgressEntry({ mode, title, headline, blockerCount, nextActs, refreshFailed, children }: Props) {
  const [open, setOpen] = useState(false);
  const triggerClass =
    "focus-ring inline-flex h-7 max-w-[22em] min-w-0 items-center gap-1.5 rounded-full border border-border px-2.5 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground data-popup-open:bg-accent data-popup-open:text-foreground";
  const triggerInner = (
    <>
      <ListChecks className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="shrink-0 font-medium text-foreground">{title}</span>
      <span className="min-w-0 truncate">{headline}</span>
      {blockerCount > 0 && (
        <span className="shrink-0 rounded-full bg-destructive/15 px-1.5 text-[11px] tabular-nums text-destructive">
          {blockerCount}
        </span>
      )}
    </>
  );
  const head = (
    <div className="flex flex-col gap-2 border-b border-border px-4 pb-3 pt-4">
      <p className="m-0 text-[12.5px] text-muted-foreground">{headline}</p>
      {refreshFailed && <p className="m-0 text-[11.5px] text-muted-foreground">{refreshFailed}</p>}
    </div>
  );
  const scroller = (
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-4 [scrollbar-gutter:stable]">{children}</div>
  );

  if (mode === "drawer") {
    return (
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger className={triggerClass}>{triggerInner}</SheetTrigger>
        <SheetContent side="right" className="gap-0 p-0 data-[side=right]:w-[520px] data-[side=right]:sm:max-w-[520px]">
          <SheetHeader className="px-4 pb-0 pt-4">
            <SheetTitle>{title}</SheetTitle>
          </SheetHeader>
          {head}
          {scroller}
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={triggerClass}>{triggerInner}</PopoverTrigger>
      <PopoverContent
        align="end"
        className="flex max-h-[min(70dvh,640px)] w-[560px] flex-col gap-0 overflow-hidden p-0"
      >
        {head}
        {scroller}
      </PopoverContent>
    </Popover>
  );
}
