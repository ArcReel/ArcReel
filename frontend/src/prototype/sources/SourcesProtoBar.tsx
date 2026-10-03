// PROTOTYPE — 原文与分集视图原型（#2982）的底部切换栏，改写自剧集页原型的切换栏。白底黑字，与被评估的暗色界面区分开。
// 只在「分集」视图与集页上出现。←/→ 切换预设（输入框聚焦时不拦截）；「逐轴」展开后可单独覆盖每条轴。

import { useEffect, useState } from "react";
import { useLocation } from "wouter";
import { AXES, AXIS_KEYS, PRESETS, type AxisKey, type AxisState } from "./axes";
import { useShellMetrics } from "../shell/store";
import { cyclePreset, setAxis, syncProtoUrl, useSourcesProto } from "./store";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

export function SourcesProtoBar() {
  const [location] = useLocation();
  const onEpisodes = /\/episodes(\/\d+)?$/.test(location);
  const scope = /\/episodes\/\d+$/.test(location) ? "episode" : "episodes";
  const { preset, overrides, axes } = useSourcesProto();
  const metrics = useShellMetrics();
  const [open, setOpen] = useState(false);

  // 应用内跳转会丢 query：每次地址变化后把当前组合写回地址栏，链接随时可分享
  useEffect(() => {
    if (onEpisodes) syncProtoUrl();
  }, [location, onEpisodes]);

  useEffect(() => {
    if (!onEpisodes) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      if (e.key === "ArrowLeft") cyclePreset(-1);
      if (e.key === "ArrowRight") cyclePreset(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onEpisodes]);

  if (!onEpisodes) return null;
  const overrideCount = Object.keys(overrides).length;
  const keys = [...AXIS_KEYS].sort((a, b) => Number(AXES[b].scope === scope) - Number(AXES[a].scope === scope));

  return (
    <div
      className="fixed bottom-4 left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900"
      style={{ colorScheme: "light" }}
    >
      {open && (
        <div className="mb-2 max-h-[70dvh] w-[min(860px,calc(100vw-32px))] overflow-y-auto rounded-xl bg-white p-3 shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
          <div className="grid gap-2">
            {keys.map((key) => (
              <AxisRow
                key={key}
                axisKey={key}
                value={axes[key]}
                dim={AXES[key].scope !== scope}
                overridden={key in overrides}
                presetValue={key === "mock" ? "none" : PRESETS[preset].axes[key]}
              />
            ))}
          </div>
          <div className="mt-2 border-t border-neutral-200 pt-2 text-[11px] leading-[1.6] text-neutral-500">
            加粗边框的选项是当前预设的取值；灰色的轴作用于另一个页面。改动会写进地址栏，复制链接即可分享当前组合。
            样本：赡养人类（剧情，12 集全部切完，集都没有脚本）、项目 66be3191（只切了 2 集，有未登记文件）、百年心结（10 集都是逐集原文）。
          </div>
        </div>
      )}
      <div className="mx-auto flex w-max items-center gap-1 rounded-full bg-white px-1.5 py-1 shadow-[0_8px_30px_rgba(0,0,0,0.5)]">
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cyclePreset(-1)} aria-label="上一个预设">
          ←
        </button>
        <span className="px-2 font-medium">
          {preset}（{PRESETS[preset].name}）
          {overrideCount > 0 && <span className="ml-1 text-neutral-500">+{overrideCount} 项覆盖</span>}
        </span>
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cyclePreset(1)} aria-label="下一个预设">
          →
        </button>
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        {metrics && (
          <span className="px-1 tabular-nums text-neutral-500">
            {metrics.viewport}px · {metrics.tier === "standard" ? "标准档" : "紧凑档"} · 画布 {metrics.canvas}
          </span>
        )}
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "收起" : "逐轴"}
        </button>
      </div>
    </div>
  );
}

function AxisRow<K extends AxisKey>({
  axisKey,
  value,
  dim,
  overridden,
  presetValue,
}: {
  axisKey: K;
  value: AxisState[K];
  dim: boolean;
  overridden: boolean;
  presetValue: AxisState[K] | string;
}) {
  const axis = AXES[axisKey];
  return (
    <div className={`flex items-center gap-3 ${dim ? "opacity-45" : ""}`}>
      <div className="w-36 shrink-0 font-medium">
        {axis.label}
        {overridden && <span className="ml-1 text-violet-600">•</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {(Object.entries(axis.values) as [AxisState[K] & string, string][]).map(([v, label]) => (
          <button
            key={v}
            type="button"
            onClick={() => setAxis(axisKey, v)}
            className={`rounded-md px-2 py-1 ${v === value ? "bg-neutral-900 text-white" : "bg-neutral-100 hover:bg-neutral-200"} ${
              v === presetValue ? "ring-2 ring-neutral-400" : ""
            }`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
