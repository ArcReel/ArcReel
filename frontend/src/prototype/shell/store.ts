// PROTOTYPE — 工作区外壳原型（#2970）的状态：预设 + 逐轴覆盖，外加布局实测值（给底栏显示）。
// 来源优先级：URL（?variant=B&s.panel=overlay）> sessionStorage > 预设 B。
// 应用内跳转会丢 query，所以状态同时留在内存与 sessionStorage。

import { useSyncExternalStore } from "react";
import { PRESETS, PRESET_KEYS, type AxisKey, type AxisState, type PresetKey } from "./axes";

interface State {
  preset: PresetKey;
  overrides: Partial<AxisState>;
}

export interface Metrics {
  viewport: number;
  tier: "standard" | "compact";
  sidebar: number;
  canvas: number;
  agent: number;
  agentMode: string;
}


// #2974：外壳已定为预设 B，固定下来；地址栏的 variant 交给剧集页原型使用。
function readInitial(): State {
  return { preset: "B", overrides: {} };
}

let state: State = readInitial();
let metrics: Metrics | null = null;
const listeners = new Set<() => void>();
const metricListeners = new Set<() => void>();

export function resolveAxes(s: State = state): AxisState {
  return { ...PRESETS[s.preset].axes, ...s.overrides };
}

function syncUrl() {}

function set(next: State) {
  state = next;
  syncUrl();
  for (const l of listeners) l();
}

export function initShellProto() {
  syncUrl();
}

export function setPreset(preset: PresetKey) {
  set({ preset, overrides: {} });
}

export function cyclePreset(step: 1 | -1) {
  const i = PRESET_KEYS.indexOf(state.preset);
  setPreset(PRESET_KEYS[(i + step + PRESET_KEYS.length) % PRESET_KEYS.length]);
}

export function setAxis<K extends AxisKey>(key: K, value: AxisState[K]) {
  const overrides = { ...state.overrides };
  if (PRESETS[state.preset].axes[key] === value) delete overrides[key];
  else overrides[key] = value;
  set({ ...state, overrides });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useShellProto() {
  const s = useSyncExternalStore(subscribe, () => state);
  return { preset: s.preset, overrides: s.overrides, axes: resolveAxes(s) };
}

export function publishMetrics(next: Metrics) {
  if (metrics && (Object.keys(next) as (keyof Metrics)[]).every((k) => metrics![k] === next[k])) return;
  metrics = next;
  for (const l of metricListeners) l();
}

export function useShellMetrics() {
  return useSyncExternalStore(
    (l) => {
      metricListeners.add(l);
      return () => metricListeners.delete(l);
    },
    () => metrics,
  );
}
