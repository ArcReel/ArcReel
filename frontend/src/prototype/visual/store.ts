// PROTOTYPE — 视觉方向原型（#2963）的状态：预设 + 逐轴覆盖。
// 来源优先级：URL（?variant=B&v.surface=glass）> sessionStorage > 预设 A。
// 应用内跳转会丢 query，所以状态同时留在内存与 sessionStorage，<html data-v-*> 跨页面保持。

import { useSyncExternalStore } from "react";
import i18n from "@/i18n";
import { AXES, AXIS_KEYS, PRESETS, PRESET_KEYS, type AxisKey, type AxisState, type PresetKey } from "./axes";

interface State {
  preset: PresetKey;
  overrides: Partial<AxisState>;
}

const STORAGE_KEY = "PROTOTYPE_visual_direction";

function isAxisValue(key: AxisKey, value: string): boolean {
  return value in AXES[key].values;
}

function readInitial(): State {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get("variant");
  if (fromUrl && (PRESET_KEYS as string[]).includes(fromUrl)) {
    const overrides: Record<string, string> = {};
    for (const key of AXIS_KEYS) {
      const v = params.get(`v.${key}`);
      if (v && isAxisValue(key, v)) overrides[key] = v;
    }
    return { preset: fromUrl as PresetKey, overrides: overrides };
  }
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as State;
  } catch {
    // 原型：读不到就用默认
  }
  return { preset: "A", overrides: {} };
}

let state: State = readInitial();
const listeners = new Set<() => void>();

export function resolveAxes(s: State = state): AxisState {
  return { ...PRESETS[s.preset].axes, ...s.overrides };
}

// 中文语言包里夹带的英文装饰后缀；eyebrow 不是 mono 时换成纯中文，切回时还原。
const LOCALIZED_EYEBROWS: Record<string, string> = {
  lobby_now_editing_eyebrow: "接着上一次",
  lobby_continue_editing_chip: "继续编辑",
  lobby_library_eyebrow: "全部项目",
  lobby_card_stat_cast: "角色",
  lobby_card_stat_scene: "场景",
  lobby_card_stat_prop: "道具",
  end_frame_picker_eyebrow: "尾帧",
};
const originalEyebrows: Record<string, string> = {};

function applyEyebrowStrings(mode: string) {
  if (!i18n.language?.startsWith("zh")) return;
  let changed = false;
  for (const [key, localized] of Object.entries(LOCALIZED_EYEBROWS)) {
    const current = i18n.getResource("zh", "dashboard", key) as string | undefined;
    if (current === undefined) continue;
    if (!(key in originalEyebrows)) originalEyebrows[key] = current;
    const next = mode === "mono" ? originalEyebrows[key] : localized;
    if (next !== current) {
      i18n.addResource("zh", "dashboard", key, next);
      changed = true;
    }
  }
  if (changed) void i18n.changeLanguage(i18n.language);
}

function apply() {
  const axes = resolveAxes();
  const root = document.documentElement;
  for (const key of AXIS_KEYS) root.setAttribute(`data-v-${key}`, String(axes[key]));
  applyEyebrowStrings(String(axes.eyebrow));

  const params = new URLSearchParams(window.location.search);
  params.set("variant", state.preset);
  for (const key of AXIS_KEYS) {
    const o = state.overrides[key];
    if (o) params.set(`v.${key}`, String(o));
    else params.delete(`v.${key}`);
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
  apply();
  for (const l of listeners) l();
}

export function initVisualProto() {
  apply();
  // dashboard 命名空间是懒加载的，加载完再补一次本地化
  i18n.on("loaded", () => applyEyebrowStrings(String(resolveAxes().eyebrow)));
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

export function useVisualProto() {
  const s = useSyncExternalStore(subscribe, () => state);
  return { preset: s.preset, overrides: s.overrides, axes: resolveAxes(s) };
}
