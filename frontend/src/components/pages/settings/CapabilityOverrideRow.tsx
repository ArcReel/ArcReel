import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { KeyboardEvent, ReactNode } from "react";

// ---------------------------------------------------------------------------
// CapabilityOverrideRow —— 视频模型行内的能力覆盖三态控件（首批仅 last_frame）
//
// 三态直接映射稀疏覆盖字典的写入形态，不引入第四种中间态：
//   follow → 覆盖字典中该键缺席，生效值跟随系统判定，判定变化时自动跟着变
//   on/off → 覆盖字典写入 { last_frame: true|false }，永久压过系统判定
// 「跟随判定」段把系统判定值内联在段内，用户在同一个控件里既看到系统怎么判的、
// 又能决定要不要改，不需要额外的说明行。
// ---------------------------------------------------------------------------

export type OverrideState = "follow" | "on" | "off";

/** 段的排布顺序，同时是键盘左右移动的顺序。 */
const SEGMENT_ORDER: OverrideState[] = ["follow", "on", "off"];

/** 覆盖值（undefined = 键缺席）与三态之间的双向映射，写入侧的唯一转换点。 */
export function overrideToState(override: boolean | undefined): OverrideState {
  if (override === undefined) return "follow";
  return override ? "on" : "off";
}

export function stateToOverride(state: OverrideState): boolean | undefined {
  if (state === "follow") return undefined;
  return state === "on";
}

interface CapabilityOverrideRowProps {
  /** 当前覆盖值；undefined = 跟随判定。 */
  override: boolean | undefined;
  /** 系统判定值；null = 尚未判定（新增或改过 model_id 的行，后端保存后才算得出）。 */
  systemValue: boolean | null;
  /** 当前 endpoint 的执行层是否下传尾帧约束；false 时「强制开」不可选（后端会 422 拒绝）。 */
  endImageCapable: boolean;
  onChange: (next: boolean | undefined) => void;
}

export function CapabilityOverrideRow({
  override,
  systemValue,
  endImageCapable,
  onChange,
}: CapabilityOverrideRowProps) {
  const { t } = useTranslation("dashboard");
  const state = overrideToState(override);
  const overridden = state !== "follow";

  const valueLabel = (v: boolean) =>
    v ? t("cap_override_value_supported") : t("cap_override_value_unsupported");
  const detectedLabel = systemValue === null ? t("cap_override_value_unknown") : valueLabel(systemValue);

  const groupRef = useRef<HTMLDivElement>(null);
  // 「强制开」在 endpoint 不下传尾帧时不可选，键盘移动要跳过它而不是停在一个点不动的段上。
  const reachable = SEGMENT_ORDER.filter((s) => s !== "on" || endImageCapable);
  // roving tabindex：整组只占一个 Tab 位，组内移动交给方向键（对齐 ModelConfigSection 的时长
  // 选择组）。当前态不可达时（理论上不会出现）退回第一个可达段，避免整组都是 -1 而 Tab 不进去。
  const tabbable = reachable.includes(state) ? state : reachable[0];

  const select = (target: OverrideState) => {
    onChange(stateToOverride(target));
    groupRef.current?.querySelector<HTMLButtonElement>(`[data-state="${target}"]`)?.focus();
  };

  // radio 组的标准键盘行为：方向键移动焦点即选中，Home/End 跳到首尾。挂在各段而非容器上——
  // 焦点本就落在段内，容器自身不该可聚焦。
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const current = reachable.indexOf(state);
    const from = current === -1 ? 0 : current;
    let next: OverrideState | undefined;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      next = reachable[(from - 1 + reachable.length) % reachable.length];
    } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      next = reachable[(from + 1) % reachable.length];
    } else if (e.key === "Home") {
      next = reachable[0];
    } else if (e.key === "End") {
      next = reachable[reachable.length - 1];
    }
    if (next === undefined) return;
    e.preventDefault();
    select(next);
  };

  const segment = (target: OverrideState, label: ReactNode, ariaLabel: string, title: string) => {
    const active = state === target;
    return (
      <button
        type="button"
        role="radio"
        aria-checked={active}
        aria-label={ariaLabel}
        data-state={target}
        disabled={!reachable.includes(target)}
        tabIndex={target === tabbable ? 0 : -1}
        title={title}
        onClick={() => select(target)}
        onKeyDown={onKeyDown}
        className="px-2 py-1 text-[10.5px] font-semibold transition-colors first:rounded-l-[6px] last:rounded-r-[6px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-45"
        style={{
          color: active ? "var(--color-accent-2)" : "var(--color-text-4)",
          background: active ? "var(--color-accent-dim)" : "var(--color-bg-grad-a)",
          border: `1px solid ${active ? "var(--color-accent-soft)" : "var(--color-hairline)"}`,
          marginLeft: -1,
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div className="mt-2 flex flex-col gap-1 pl-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3 whitespace-nowrap">
          {t("cap_override_last_frame_label")}
        </span>

        <div
          ref={groupRef}
          className="flex items-center"
          role="radiogroup"
          aria-label={t("cap_override_group_label")}
        >
          {segment(
            "follow",
            <span>
              {t("cap_override_follow")}
              <span className="ml-1 opacity-70">·{detectedLabel}</span>
            </span>,
            // 可访问名带上判定值，读屏用户与视觉用户听到／看到的是同一句
            `${t("cap_override_follow")}·${detectedLabel}`,
            t("cap_override_follow_title"),
          )}
          {segment("on", t("cap_override_on"), t("cap_override_on"), t("cap_override_on_title"))}
          {segment("off", t("cap_override_off"), t("cap_override_off"), t("cap_override_off_title"))}
        </div>

        {overridden && (
          <span
            className="rounded px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.05em]"
            style={{
              color: "var(--color-warm-bright)",
              background: "var(--color-warm-tint)",
              border: "1px solid var(--color-warm-ring)",
            }}
          >
            {t("cap_override_badge")}
          </span>
        )}

        {overridden && (
          <span className="text-[10px] text-text-4">
            {t("cap_override_effective", {
              effective: valueLabel(state === "on"),
              detected: detectedLabel,
            })}
          </span>
        )}
      </div>

      {/* title 对键盘与触屏不可达，禁用原因必须有一行可见说明 */}
      {!endImageCapable && <p className="text-[11px] text-text-4">{t("cap_override_on_unavailable")}</p>}
    </div>
  );
}
