import { useLayoutEffect, useRef, type CSSProperties } from "react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { MoveHorizontal, Scissors, X } from "lucide-react";

import type { EpisodeMeta, EpisodesView } from "@/types";
import { episodeDisplayName } from "@/utils/episode-display";

import { Button } from "@/components/ui/button";

import { episodeHue, formatVolume } from "./episodes-view-model";
import { rangeUnits, type PointAction } from "./manual-split-model";

interface ManualSplitToolbarProps {
  view: EpisodesView;
  episodes: EpisodeMeta[];
  action: PointAction;
  title: string;
  onTitleChange: (title: string) => void;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** 插入光标取哪一集的集色：拆分取这一集，切分与移动分界取品牌色（null）。 */
export function caretEpisode(action: PointAction): number | null {
  return action.kind === "split" ? action.episode : null;
}

/**
 * 插入光标下方的浮动操作条：预览分出的体量，确认或取消。←/→ 微调与 Enter / Esc 由视图统一接管。
 */
export function ManualSplitToolbar({
  view,
  episodes,
  action,
  title,
  onTitleChange,
  busy,
  onConfirm,
  onCancel,
}: ManualSplitToolbarProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const volume = (start: number, end: number) => formatVolume(t, rangeUnits(view, start, end), view.unit);
  const tinted = caretEpisode(action);
  const iconCls = cn("size-3.5 shrink-0", tinted === null ? "text-primary" : "text-episode");
  const ref = useRef<HTMLSpanElement>(null);

  // 操作条默认从光标左侧 24px 起；靠近行首或行尾时夹在原文列之内，确认与取消按钮始终可见
  useLayoutEffect(() => {
    const el = ref.current;
    const anchor = el?.parentElement;
    const column = el?.closest("[data-manuscript]");
    if (!el || !anchor || !column) return;
    const caretX = anchor.getBoundingClientRect().left;
    const bounds = column.getBoundingClientRect();
    const left = Math.max(bounds.left - caretX, Math.min(-24, bounds.right - caretX - el.offsetWidth));
    el.style.left = `${left}px`;
  });

  let summary;
  let confirmLabel: string;
  if (action.kind === "cut") {
    summary = (
      <>
        <Scissors className={iconCls} aria-hidden />
        <span className="text-muted-foreground">{t("dashboard:manual_split_cut_summary", { volume: volume(action.start, action.end) })}</span>
        <input
          aria-label={t("dashboard:manual_split_title_label")}
          placeholder={t("dashboard:manual_split_title_placeholder")}
          className="w-32 border-b border-input bg-transparent px-1 text-foreground outline-none placeholder:text-muted-foreground focus-visible:border-primary"
          value={title}
          onChange={(event) => onTitleChange(event.target.value)}
        />
      </>
    );
    confirmLabel = t("dashboard:manual_split_cut_confirm");
  } else if (action.kind === "split") {
    summary = (
      <>
        <Scissors className={iconCls} aria-hidden />
        <span className="text-muted-foreground">
          {t("dashboard:manual_split_split_summary", {
            name: episodeDisplayName(episodes, action.episode, t),
            front: volume(action.start, action.at),
            back: volume(action.at, action.end),
          })}
        </span>
      </>
    );
    confirmLabel = t("dashboard:manual_split_split_confirm");
  } else {
    const forward = action.at > action.boundary;
    summary = (
      <>
        <MoveHorizontal className={iconCls} aria-hidden />
        <span className="text-muted-foreground">
          {t("dashboard:manual_split_move_summary", {
            left: episodeDisplayName(episodes, action.left, t),
            leftVolume: volume(action.start, action.at),
            right: episodeDisplayName(episodes, action.right, t),
            rightVolume: volume(action.at, action.end),
          })}
          <span className="ml-1">
            {t(forward ? "dashboard:manual_split_move_later" : "dashboard:manual_split_move_earlier", {
              volume: volume(Math.min(action.at, action.boundary), Math.max(action.at, action.boundary)),
            })}
          </span>
        </span>
      </>
    );
    confirmLabel = t("dashboard:manual_split_move_confirm");
  }

  return (
    <span
      ref={ref}
      data-no-caret
      data-manual-split-toolbar
      role="toolbar"
      aria-label={t("dashboard:manual_split_toolbar_label")}
      className={cn(
        "absolute top-full -left-6 z-sticky mt-1.5 inline-flex items-center gap-2.5 rounded-md border bg-popover px-2.5 py-1 font-sans text-xs leading-normal whitespace-nowrap shadow-overlay",
        tinted === null ? "border-primary" : "border-episode",
      )}
      style={{ "--episode-hue": episodeHue(tinted ?? 0) } as CSSProperties}
    >
      {summary}
      <span className="text-muted-foreground">{t("dashboard:manual_split_nudge_hint")}</span>
      <Button size="xs" disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </Button>
      <Button variant="ghost" size="icon-xs" aria-label={t("common:cancel")} onClick={onCancel}>
        <X aria-hidden />
      </Button>
    </span>
  );
}
