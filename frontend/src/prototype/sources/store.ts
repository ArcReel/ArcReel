// PROTOTYPE — 原文与分集视图原型（#2982）的状态：预设 + 逐轴覆盖。
// 来源优先级：URL（?variant=B&s.layout=outline）> sessionStorage > 预设 B。
// 应用内跳转会丢 query，所以状态同时留在内存与 sessionStorage，每次地址变化后再写回地址栏。

import { useSyncExternalStore } from "react";
import { AXES, AXIS_KEYS, PRESETS, PRESET_KEYS, type AxisKey, type AxisState, type PresetKey } from "./axes";

interface State {
  preset: PresetKey;
  overrides: Partial<AxisState>;
}

const STORAGE_KEY = "PROTOTYPE_sources_view";

function isAxisValue(key: AxisKey, value: string): boolean {
  return value in AXES[key].values;
}

function readInitial(): State {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get("variant");
  if (fromUrl && (PRESET_KEYS as string[]).includes(fromUrl)) {
    const overrides: Record<string, string> = {};
    for (const key of AXIS_KEYS) {
      const v = params.get(`s.${key}`);
      if (v && isAxisValue(key, v)) overrides[key] = v;
    }
    return { preset: fromUrl as PresetKey, overrides };
  }
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as State;
  } catch {
    // 原型：读不到就用默认
  }
  return { preset: "E", overrides: {} };
}

let state: State = readInitial();
const listeners = new Set<() => void>();

export function resolveAxes(s: State = state): AxisState {
  return { mock: "none", ...PRESETS[s.preset].axes, ...s.overrides };
}

export function syncProtoUrl() {
  const params = new URLSearchParams(window.location.search);
  params.set("variant", state.preset);
  for (const key of AXIS_KEYS) {
    const o = state.overrides[key];
    if (o) params.set(`s.${key}`, String(o));
    else params.delete(`s.${key}`);
  }
  const next = `${window.location.pathname}?${params.toString()}`;
  if (next !== `${window.location.pathname}${window.location.search}`) {
    window.history.replaceState(window.history.state, "", next);
  }
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore
  }
}

function set(next: State) {
  state = next;
  syncProtoUrl();
  for (const l of listeners) l();
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
  const presetValue = key === "mock" ? "none" : (PRESETS[state.preset].axes as Record<string, string>)[key];
  if (presetValue === value) delete overrides[key];
  else overrides[key] = value;
  set({ ...state, overrides });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useSourcesProto() {
  const s = useSyncExternalStore(subscribe, () => state);
  return { preset: s.preset, overrides: s.overrides, axes: resolveAxes(s) };
}
