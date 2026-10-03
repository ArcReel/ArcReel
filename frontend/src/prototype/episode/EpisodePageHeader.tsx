// PROTOTYPE — 剧集页原型（#2974）：合并后的页头。
// oneRow：集头 | 脚本规划 · 分镜 · 剪辑 | 动作 | 制作进度，全部一行；
// twoRow：第一行集头与制作进度，第二行 tab 与当前 tab 的动作。
// 集头与动作来自画布的 portal（见 slots.tsx），这里只摆放插槽并渲染 tab。

import type { ReactNode } from "react";
import { requestCanvasTab, setSlot, useCanvasTabState } from "./slots";
import { setAdSettingTab, useAdSettingTab } from "../overview/adSettingTab";

type Tab = "setting" | "plan" | "board" | "edit";

export function EpisodePageHeader({
  mode,
  canEdit,
  editActive,
  onEditChange,
  progress,
  settingTab = false,
}: {
  mode: "oneRow" | "twoRow";
  canEdit: boolean;
  editActive: boolean;
  onEditChange: (edit: boolean) => void;
  progress: ReactNode;
  /** #2981：广告项目在视频页加「故事设定」tab */
  settingTab?: boolean;
}) {
  const canvas = useCanvasTabState();
  const settingActive = useAdSettingTab() && settingTab;
  const active: Tab | null = settingActive ? "setting" : editActive ? "edit" : canvas.current;
  const tabs: { key: Tab; label: string; disabled?: boolean }[] = [];
  if (settingTab) tabs.push({ key: "setting", label: "故事设定" });
  if (canvas.hasPlan) tabs.push({ key: "plan", label: "脚本规划" });
  if (canvas.current) tabs.push({ key: "board", label: canvas.boardLabel, disabled: !canvas.boardEnabled });
  if (canEdit) tabs.push({ key: "edit", label: "剪辑" });

  const choose = (key: Tab) => {
    setAdSettingTab(key === "setting");
    if (key === "setting") return;
    if (key === "edit") {
      onEditChange(true);
      return;
    }
    onEditChange(false);
    requestCanvasTab(key);
  };

  const tabList = tabs.length > 1 && (
    <div role="tablist" aria-label="剧集视图" className="flex shrink-0 items-center gap-0.5 rounded-lg bg-muted/60 p-0.5">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={active === tab.key}
          disabled={tab.disabled}
          onClick={() => choose(tab.key)}
          className={`focus-ring rounded-md px-3 py-1 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
            active === tab.key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );

  const head = <div ref={(el) => setSlot("head", el)} className="flex min-w-0 flex-1 items-center" />;
  // 单行页头放不下时批量按钮只留图标；两行页头的第二行有空间，文字一直显示
  const actions = (
    <div
      ref={(el) => setSlot("actions", el)}
      className={`flex min-w-0 shrink-0 items-center gap-1.5 [&_.sv-navbtn]:whitespace-nowrap ${
        mode === "oneRow" ? "@max-[1240px]/ephdr:[&_.sv-navbtn>span]:sr-only" : ""
      }`}
    />
  );

  if (mode === "oneRow") {
    return (
      <div className="@container/ephdr flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
        {head}
        {tabList}
        {actions}
        {progress}
      </div>
    );
  }
  return (
    <div className="@container/ephdr shrink-0 border-b border-border">
      <div className="flex h-11 items-center gap-3 px-4">
        {head}
        {progress}
      </div>
      <div className="flex h-10 items-center gap-3 px-4">
        {tabList}
        <span className="flex-1" />
        {actions}
      </div>
    </div>
  );
}
