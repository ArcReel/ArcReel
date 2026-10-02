// PROTOTYPE — #2972 的底部切换栏。白底黑字，与被评估的暗色界面区分开。
// ←/→ 切换预设（输入框聚焦时不拦截）；「逐轴」展开后可单独覆盖每条轴；读数每 400ms 量一次。

import { useEffect, useState } from "react";
import { AXES, AXIS_KEYS, PRESETS, type AxisKey, type AxisState } from "./axes";
import { cyclePreset, setAxis, syncProtoUrl, toggleSamples, useProto } from "./store";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

function measure() {
  const w = (sel: string) => {
    const el = document.querySelector<HTMLElement>(sel);
    return el ? Math.round(el.getBoundingClientRect().width) : null;
  };
  const doc = document.scrollingElement ?? document.documentElement;
  return {
    viewport: `${window.innerWidth}×${window.innerHeight}`,
    standard: window.innerWidth >= 1280,
    rail: w('[data-zone="rail"]'),
    detail: w('[data-zone="detail"]'),
    inspector: w('[data-zone="inspector"]'),
    docScrolls: doc.scrollHeight > window.innerHeight + 1,
  };
}

export function ProtoBar() {
  const { preset, overrides, axes, samples } = useProto();
  const [open, setOpen] = useState(false);
  const [m, setM] = useState(measure);

  useEffect(() => {
    syncProtoUrl();
    const id = window.setInterval(() => {
      setM(measure());
      // 设置页切换分区会改写 query，补回原型参数
      if (!new URLSearchParams(window.location.search).has("variant")) syncProtoUrl();
    }, 400);
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      if (e.key === "ArrowLeft") cyclePreset(-1);
      if (e.key === "ArrowRight") cyclePreset(1);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const overrideCount = Object.keys(overrides).length;

  return (
    <div className="fixed top-2.5 left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900" style={{ colorScheme: "light" }}>
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
        <span className="px-1 text-neutral-500 tabular-nums">
          {m.viewport}，{m.standard ? "标准档" : "紧凑档"}
          {m.rail !== null && `，二级栏 ${m.rail}`}
          {m.detail !== null && `，详情 ${m.detail}`}
          {m.inspector !== null && `，检查器 ${m.inspector}`}
          {m.docScrolls && <span className="ml-1 text-red-600">文档在滚动</span>}
        </span>
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "收起" : "逐轴"}
        </button>
      </div>
      {open && (
        <div className="mt-2 max-h-[70dvh] w-[min(860px,calc(100vw-32px))] overflow-y-auto rounded-xl bg-white p-3 shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
          <div className="grid gap-2">
            {AXIS_KEYS.map((key) => (
              <AxisRow key={key} axisKey={key} value={axes[key]} overridden={key in overrides} presetValue={PRESETS[preset].axes[key]} />
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2 border-t border-neutral-200 pt-2 text-[11px] text-neutral-500">
            <span className="flex-1">
              灰色描边是当前预设的取值；改动写进地址栏，复制链接即可分享组合。紧凑档用窗口宽度 &lt;1280 触发。所有保存、删除、安装都是桩，不写后端。
            </span>
            <button type="button" className="shrink-0 rounded-md bg-neutral-100 px-2 py-1 hover:bg-neutral-200" onClick={toggleSamples}>
              {samples ? "隐藏样例数据" : "混入样例数据"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AxisRow<K extends AxisKey>({ axisKey, value, overridden, presetValue }: { axisKey: K; value: AxisState[K]; overridden: boolean; presetValue: AxisState[K] }) {
  const axis = AXES[axisKey];
  return (
    <div className="flex items-center gap-3">
      <div className="w-20 shrink-0 font-medium">
        {axis.label}
        {overridden && <span className="ml-1 text-violet-600">•</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {(Object.entries(axis.values) as [AxisState[K] & string, string][]).map(([v, label]) => (
          <button
            key={v}
            type="button"
            onClick={() => setAxis(axisKey, v)}
            className={`rounded-md px-2 py-1 ${v === value ? "bg-neutral-900 text-white" : "bg-neutral-100 hover:bg-neutral-200"} ${v === presetValue ? "ring-2 ring-neutral-400" : ""}`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
