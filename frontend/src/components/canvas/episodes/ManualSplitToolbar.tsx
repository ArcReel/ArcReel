import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { MoveHorizontal, Scissors, X } from "lucide-react";

import type { EpisodeMeta, EpisodesView } from "@/types";
import { episodeDisplayName } from "@/utils/episode-display";

import { episodeColor, formatVolume } from "./episodes-view-model";
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

/** 插入光标的颜色：拆分取这一集的集色，切分与移动分界取强调色。 */
export function caretColor(action: PointAction): string {
  return action.kind === "split" ? episodeColor(action.episode) : "var(--color-accent)";
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
  const file = view.files[action.file];
  const volume = (start: number, end: number) => formatVolume(t, rangeUnits(file, start, end, view.unit), view.unit);
  const color = caretColor(action);
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
        <Scissors className="h-3.5 w-3.5 shrink-0" style={{ color }} aria-hidden />
        <span className="text-text-3">{t("dashboard:manual_split_cut_summary", { volume: volume(action.start, action.end) })}</span>
        <input
          aria-label={t("dashboard:manual_split_title_label")}
          placeholder={t("dashboard:manual_split_title_placeholder")}
          className="w-32 border-b border-hairline-strong bg-transparent px-1 text-text outline-none placeholder:text-text-4 focus:border-accent"
          value={title}
          onChange={(event) => onTitleChange(event.target.value)}
        />
      </>
    );
    confirmLabel = t("dashboard:manual_split_cut_confirm");
  } else if (action.kind === "split") {
    summary = (
      <>
        <Scissors className="h-3.5 w-3.5 shrink-0" style={{ color }} aria-hidden />
        <span className="text-text-3">
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
        <MoveHorizontal className="h-3.5 w-3.5 shrink-0" style={{ color }} aria-hidden />
        <span className="text-text-3">
          {t("dashboard:manual_split_move_summary", {
            left: episodeDisplayName(episodes, action.left, t),
            leftVolume: volume(action.start, action.at),
            right: episodeDisplayName(episodes, action.right, t),
            rightVolume: volume(action.at, action.end),
          })}
          <span className="ml-1 text-text-4">
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
      className="absolute -left-6 top-full z-20 mt-1.5 inline-flex items-center gap-2.5 whitespace-nowrap rounded-md px-2.5 py-1 text-[12px] leading-normal [font-family:var(--font-sans)]"
      style={{
        background: "oklch(0.2 0.015 265 / 0.97)",
        border: `1px solid ${color}`,
        boxShadow: "0 10px 28px -8px oklch(0 0 0 / 0.7)",
      }}
    >
      {summary}
      <span className="text-[11px] text-text-4">{t("dashboard:manual_split_nudge_hint")}</span>
      <button
        type="button"
        disabled={busy}
        onClick={onConfirm}
        className="focus-ring rounded font-medium disabled:opacity-50"
        style={{ color }}
      >
        {confirmLabel}
      </button>
      <button
        type="button"
        aria-label={t("common:cancel")}
        onClick={onCancel}
        className="focus-ring rounded p-0.5 text-text-4 hover:bg-[oklch(0.3_0.01_265)] hover:text-text"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </span>
  );
}
