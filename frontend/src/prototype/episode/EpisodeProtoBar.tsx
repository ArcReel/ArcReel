// PROTOTYPE — 剧集页原型（#2974）的底部切换栏，改写自工作区外壳原型的切换栏。白底黑字，与被评估的暗色界面区分开。
// ←/→ 切换预设（输入框聚焦时不拦截）；「逐轴」展开后可单独覆盖每条轴；实测宽度来自外壳原型。

import { useEffect, useState } from "react";
import { AXES, AXIS_KEYS, PRESETS, type AxisKey, type AxisState } from "./axes";
import { useShellMetrics } from "../shell/store";
import { cyclePreset, setAxis, useEpisodeProto } from "./store";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

export function EpisodeProtoBar() {
  const { preset, overrides, axes } = useEpisodeProto();
  const metrics = useShellMetrics();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      if (e.key === "ArrowLeft") cyclePreset(-1);
      if (e.key === "ArrowRight") cyclePreset(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const overrideCount = Object.keys(overrides).length;

  return (
    <div
      className="fixed bottom-4 left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900"
      style={{ colorScheme: "light" }}
    >
      {open && (
        <div className="mb-2 max-h-[70dvh] w-[min(820px,calc(100vw-32px))] overflow-y-auto rounded-xl bg-white p-3 shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
          <div className="grid gap-2">
            {AXIS_KEYS.map((key) => (
              <AxisRow key={key} axisKey={key} value={axes[key]} overridden={key in overrides} presetValue={PRESETS[preset].axes[key]} />
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2 border-t border-neutral-200 pt-2 text-[11px] text-neutral-500">
            <span className="flex-1">
              加粗边框的选项是当前预设的取值；改动会写进地址栏，复制链接即可分享当前组合。外壳固定为「工作区外壳」结论；
              分镜样本：凌晨四点的荔枝档（剧情）、百年心结（旁白）；参考视频与剪辑样本：赡养人类第 8 集。
            </span>
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
          <span className="tabular-nums px-1 text-neutral-500">
            {metrics.viewport}px · {metrics.tier === "standard" ? "标准档" : "紧凑档"} · 侧栏 {metrics.sidebar} / 画布 {metrics.canvas} / Agent{" "}
            {metrics.agent}（{metrics.agentMode}）
          </span>
        )}
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <button
          type="button"
          className="rounded-full px-2.5 py-1 hover:bg-neutral-100"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "收起" : "逐轴"}
        </button>
      </div>
    </div>
  );
}

function AxisRow<K extends AxisKey>({
  axisKey,
  value,
  overridden,
  presetValue,
}: {
  axisKey: K;
  value: AxisState[K];
  overridden: boolean;
  presetValue: AxisState[K];
}) {
  const axis = AXES[axisKey];
  return (
    <div className="flex items-center gap-3">
      <div className="w-32 shrink-0 font-medium">
        {axis.label}
        {overridden && <span className="ml-1 text-violet-600">•</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {(Object.entries(axis.values) as [AxisState[K] & string, string][]).map(([v, label]) => (
          <button
            key={v}
            type="button"
            onClick={() => setAxis(axisKey, v)}
            className={`rounded-md px-2 py-1 ${
              v === value ? "bg-neutral-900 text-white" : "bg-neutral-100 hover:bg-neutral-200"
            } ${v === presetValue ? "ring-2 ring-neutral-400" : ""}`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
