// PROTOTYPE — #2972 的原型状态：预设 + 逐轴覆盖。来源优先级：URL（?variant=B&p.models=cards）> sessionStorage > 预设 A。
// 设置页内切换分区会改写 query，所以状态同时留在内存与 sessionStorage，切回来时再写回地址栏。

import { useSyncExternalStore } from "react";
import { AXES, AXIS_KEYS, PRESETS, PRESET_KEYS, type AxisKey, type AxisState, type PresetKey } from "./axes";

interface State {
  preset: PresetKey;
  overrides: Partial<AxisState>;
  /** 是否混入「原型样例」数据（真实数据为空时默认开）。 */
  samples: boolean;
}

const STORAGE_KEY = "PROTOTYPE_providers_market";

function readInitial(): State {
  let stored: State | null = null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) stored = JSON.parse(raw) as State;
  } catch {
    // 原型：读不到就用默认
  }
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get("variant");
  if (fromUrl && (PRESET_KEYS as string[]).includes(fromUrl)) {
    const overrides: Record<string, string> = {};
    for (const key of AXIS_KEYS) {
      const v = params.get(`p.${key}`);
      if (v && v in AXES[key].values) overrides[key] = v;
    }
    return { preset: fromUrl as PresetKey, overrides, samples: params.get("samples") !== "0" };
  }
  return stored ?? { preset: "A", overrides: {}, samples: true };
}

let state: State = readInitial();
const listeners = new Set<() => void>();

export function syncProtoUrl() {
  const params = new URLSearchParams(window.location.search);
  params.set("variant", state.preset);
  for (const key of AXIS_KEYS) {
    const o = state.overrides[key];
    if (o) params.set(`p.${key}`, String(o));
    else params.delete(`p.${key}`);
  }
  if (state.samples) params.delete("samples");
  else params.set("samples", "0");
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

export function cyclePreset(dir: 1 | -1) {
  const i = PRESET_KEYS.indexOf(state.preset);
  set({ ...state, preset: PRESET_KEYS[(i + dir + PRESET_KEYS.length) % PRESET_KEYS.length], overrides: {} });
}

export function setAxis<K extends AxisKey>(key: K, value: AxisState[K]) {
  const overrides = { ...state.overrides };
  if (PRESETS[state.preset].axes[key] === value) delete overrides[key];
  else overrides[key] = value;
  set({ ...state, overrides });
}

export function toggleSamples() {
  set({ ...state, samples: !state.samples });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useProto() {
  const s = useSyncExternalStore(subscribe, () => state);
  return { ...s, axes: { ...PRESETS[s.preset].axes, ...s.overrides } };
}

export function useAxis<K extends AxisKey>(key: K): AxisState[K] {
  return useProto().axes[key];
}
