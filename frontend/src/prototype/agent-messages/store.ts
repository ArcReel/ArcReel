// PROTOTYPE — Agent 面板消息区原型（#2980）的状态：预设 + 逐轴覆盖。
// 来源优先级：URL（?variant=B&s.work=grouped）> sessionStorage > 预设 B。
// 应用内跳转会丢 query，所以状态同时留在内存与 sessionStorage。

import { useSyncExternalStore } from "react";
import { AXES, AXIS_KEYS, PRESETS, PRESET_KEYS, type AxisKey, type AxisState, type PresetKey } from "./axes";

interface State {
  preset: PresetKey;
  overrides: Partial<AxisState>;
}

const STORAGE_KEY = "PROTOTYPE_agent_messages";

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
  return { preset: "B", overrides: {} };
}

let state: State = readInitial();
const listeners = new Set<() => void>();

export function resolveAxes(s: State = state): AxisState {
  return { ...PRESETS[s.preset].axes, ...s.overrides };
}

function syncUrl() {
  const params = new URLSearchParams(window.location.search);
  params.set("variant", state.preset);
  for (const key of AXIS_KEYS) {
    const o = state.overrides[key];
    if (o) params.set(`s.${key}`, String(o));
    else params.delete(`s.${key}`);
  }
  window.history.replaceState(window.history.state, "", `${window.location.pathname}?${params.toString()}`);
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore
  }
}

function set(next: State) {
  state = next;
  syncUrl();
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
  if (PRESETS[state.preset].axes[key] === value) delete overrides[key];
  else overrides[key] = value;
  set({ ...state, overrides });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useMsgProto() {
  const s = useSyncExternalStore(subscribe, () => state);
  return { preset: s.preset, overrides: s.overrides, axes: resolveAxes(s) };
}

// 切换栏的宽度按钮 → StudioLayout 的 Agent 面板宽度。带序号，重复点同一宽度也生效。
let widthRequest: { width: number; seq: number } | null = null;
const widthListeners = new Set<() => void>();

export function requestAgentWidth(width: number) {
  widthRequest = { width, seq: (widthRequest?.seq ?? 0) + 1 };
  for (const l of widthListeners) l();
}

export function useRequestedAgentWidth() {
  return useSyncExternalStore(
    (l) => {
      widthListeners.add(l);
      return () => widthListeners.delete(l);
    },
    () => widthRequest,
  );
}
