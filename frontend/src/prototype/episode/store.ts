// PROTOTYPE — 剧集页原型（#2974）的状态：预设 + 逐轴覆盖。
// 来源优先级：URL（?variant=B&s.editor=fourCol）> sessionStorage > 预设 B。
// 应用内跳转会丢 query，所以状态同时留在内存与 sessionStorage。

import { useSyncExternalStore } from "react";
import { PRESETS, PRESET_KEYS, type AxisKey, type AxisState, type PresetKey } from "./axes";

interface State {
  preset: PresetKey;
  overrides: Partial<AxisState>;
}


// #2982：剧集页已定为预设 D，固定下来；地址栏的 variant 交给原文与分集视图原型使用。
function readInitial(): State {
  return { preset: "D", overrides: {} };
}

let state: State = readInitial();
const listeners = new Set<() => void>();

export function resolveAxes(s: State = state): AxisState {
  return { ...PRESETS[s.preset].axes, ...s.overrides };
}

function syncUrl() {}

function set(next: State) {
  state = next;
  syncUrl();
  for (const l of listeners) l();
}

export function initEpisodeProto() {
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

export function useEpisodeProto() {
  const s = useSyncExternalStore(subscribe, () => state);
  return { preset: s.preset, overrides: s.overrides, axes: resolveAxes(s) };
}
