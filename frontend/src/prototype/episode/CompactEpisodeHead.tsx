// PROTOTYPE — 剧集页原型（#2974）：合并页头里的单行集头。
// 「EP · 01 标题 · 22 分镜 · ~118s · 进度 · 预估」排成一行；删除这一集收进「更多」菜单，费用细项进 title 提示。

import type { ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";
import { EpisodeDeleteButton } from "@/components/canvas/episodes/EpisodeDeleteButton";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function CompactEpisodeHead({
  chip,
  title,
  meta,
  progress,
  cost,
  costDetail,
  episode,
}: {
  chip: string;
  title: ReactNode;
  meta: string;
  progress?: string;
  cost?: string;
  costDetail?: string;
  episode: number;
}) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2.5">
      <span className="shrink-0 rounded bg-primary/15 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-primary">
        {chip}
      </span>
      <div className="min-w-0 max-w-[12em] shrink-0 [&_h1]:text-[15px] [&_h1]:font-semibold [&_h1]:leading-tight">{title}</div>
      <span className="min-w-0 shrink truncate whitespace-nowrap text-[12px] tabular-nums text-muted-foreground">
        {meta}
        {progress && <> · {progress}</>}
        {cost && (
          <span title={costDetail}>
            {" "}· 预估 <span className="text-foreground">{cost}</span>
          </span>
        )}
      </span>
      <Popover>
        <PopoverTrigger
          className="focus-ring inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="本集操作"
        >
          <MoreHorizontal className="h-4 w-4" />
        </PopoverTrigger>
        <PopoverContent align="start" className="w-auto p-1.5">
          <EpisodeDeleteButton episode={episode} />
        </PopoverContent>
      </Popover>
    </div>
  );
}
