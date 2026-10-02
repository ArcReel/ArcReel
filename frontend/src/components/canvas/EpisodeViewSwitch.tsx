import { useRef, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { EPISODE_VIEW_EDIT, EPISODE_VIEW_PARAM } from "@/app-routes";

export type EpisodeView = "storyboard" | "edit";

export function episodeViewOf(params: URLSearchParams): EpisodeView {
  return params.get(EPISODE_VIEW_PARAM) === EPISODE_VIEW_EDIT ? "edit" : "storyboard";
}

/** 切换控制的内容区；集页把它挂在「分镜 / 剪辑」下方的容器上，并用 {@link episodeViewTabId} 关联当前标签。 */
export const EPISODE_VIEW_PANEL_ID = "episode-view-panel";

export function episodeViewTabId(view: EpisodeView): string {
  return `episode-view-${view}-tab`;
}

const VIEWS: readonly EpisodeView[] = ["storyboard", "edit"];

interface EpisodeViewSwitchProps {
  view: EpisodeView;
  onChange: (view: EpisodeView) => void;
}

/** 集页顶部的「分镜 / 剪辑」切换。 */
export function EpisodeViewSwitch({ view, onChange }: EpisodeViewSwitchProps) {
  const { t } = useTranslation("dashboard");
  const tabs = useRef(new Map<EpisodeView, HTMLButtonElement>());
  // tablist 的键盘约定：Tab 进出控件组，方向键在组内切换。
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = VIEWS[(VIEWS.indexOf(view) + step + VIEWS.length) % VIEWS.length];
    onChange(next);
    tabs.current.get(next)?.focus();
  };
  const tab = (key: EpisodeView, label: string) => (
    <button
      ref={(node) => {
        if (node) tabs.current.set(key, node);
        else tabs.current.delete(key);
      }}
      type="button"
      role="tab"
      id={episodeViewTabId(key)}
      aria-selected={view === key}
      aria-controls={EPISODE_VIEW_PANEL_ID}
      tabIndex={view === key ? 0 : -1}
      onClick={() => onChange(key)}
      onKeyDown={onKeyDown}
      className="focus-ring relative px-3.5 py-2 text-[12.5px] font-medium transition-colors"
      style={{ color: view === key ? "var(--color-text)" : "var(--color-text-3)" }}
    >
      {label}
      {view === key && (
        <span
          aria-hidden="true"
          className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded"
          style={{ background: "var(--color-accent)" }}
        />
      )}
    </button>
  );
  return (
    <div
      role="tablist"
      aria-label={t("episode_view_aria")}
      className="flex items-center gap-0.5 border-b px-3"
      style={{ borderColor: "var(--color-hairline)" }}
    >
      {tab("storyboard", t("episode_view_storyboard"))}
      {tab("edit", t("episode_view_edit"))}
    </div>
  );
}
