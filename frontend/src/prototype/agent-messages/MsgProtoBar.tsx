// PROTOTYPE — 消息区原型（#2980）的底部切换栏，改写自剧集页原型的切换栏。白底黑字，与被评估的暗色界面区分开。
// ←/→ 切换预设（输入框聚焦时不拦截）；「逐轴」展开后可单独覆盖每条轴；「场景」往消息区注入样本；宽度按钮直接设定 Agent 面板宽度。

import { useEffect, useState } from "react";
import "./proto.css";
import { AXES, AXIS_KEYS, PRESETS, WIDTH_STOPS, type AxisKey, type AxisState } from "./axes";
import { useShellMetrics } from "../shell/store";
import { cyclePreset, requestAgentWidth, setAxis, useMsgProto } from "./store";
import {
  addFailure,
  addImageMessage,
  addLongCode,
  addTodos,
  addToolError,
  askQuestion,
  playStreaming,
  restoreScenario,
  setContext,
  stopStreaming,
} from "./scenarios";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

const SCENARIOS: Array<[string, () => void]> = [
  ["流式回放", () => playStreaming()],
  ["中断", stopStreaming],
  ["提问", askQuestion],
  ["待办", addTodos],
  ["工具报错", addToolError],
  ["Agent 故障", addFailure],
  ["带图消息", addImageMessage],
  ["代码与表格", addLongCode],
  ["上下文", setContext],
  ["复原", restoreScenario],
];

export function MsgProtoBar() {
  const { preset, overrides, axes } = useMsgProto();
  const metrics = useShellMetrics();
  const [open, setOpen] = useState<"axes" | "scenes" | null>(null);

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
      {open === "axes" && (
        <div className="mb-2 max-h-[70dvh] w-[min(860px,calc(100vw-32px))] overflow-y-auto rounded-xl bg-white p-3 shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
          <div className="grid gap-2">
            {AXIS_KEYS.map((key) => (
              <AxisRow key={key} axisKey={key} value={axes[key]} overridden={key in overrides} presetValue={PRESETS[preset].axes[key]} />
            ))}
          </div>
          <div className="mt-2 border-t border-neutral-200 pt-2 text-[11px] text-neutral-500">
            加粗边框的选项是当前预设的取值；改动会写进地址栏，复制链接即可分享当前组合。外壳固定为「工作区外壳」结论。
            样本会话：赡养人类 · 第 8 集生成与审片（真实会话），缺的消息类型用「场景」注入，只改内存。
          </div>
        </div>
      )}
      {open === "scenes" && (
        <div className="mb-2 w-[min(860px,calc(100vw-32px))] rounded-xl bg-white p-3 shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
          <div className="flex flex-wrap gap-1">
            {SCENARIOS.map(([label, fn]) => (
              <button key={label} type="button" className="rounded-md bg-neutral-100 px-2 py-1 hover:bg-neutral-200" onClick={fn}>
                {label}
              </button>
            ))}
          </div>
          <div className="mt-2 text-[11px] text-neutral-500">
            「流式回放」约 15 秒：思考 → 读计划 → 待办 → 生成视频 → 正文逐字增长。回放中往上翻，检查是否停止跟随、出现「跳到最新」。
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
        <span className="px-1 text-neutral-500">Agent 宽</span>
        {WIDTH_STOPS.map((w) => (
          <button
            key={w}
            type="button"
            onClick={() => requestAgentWidth(w)}
            className={`rounded-full px-2 py-1 tabular-nums ${metrics?.agent === w ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"}`}
          >
            {w}
          </button>
        ))}
        {metrics && <span className="tabular-nums px-1 text-neutral-500">实测 {metrics.agent}</span>}
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <button
          type="button"
          className="rounded-full px-2.5 py-1 hover:bg-neutral-100"
          aria-expanded={open === "scenes"}
          onClick={() => setOpen((v) => (v === "scenes" ? null : "scenes"))}
        >
          场景
        </button>
        <button
          type="button"
          className="rounded-full px-2.5 py-1 hover:bg-neutral-100"
          aria-expanded={open === "axes"}
          onClick={() => setOpen((v) => (v === "axes" ? null : "axes"))}
        >
          {open === "axes" ? "收起" : "逐轴"}
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
      <div className="w-28 shrink-0 font-medium">
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
