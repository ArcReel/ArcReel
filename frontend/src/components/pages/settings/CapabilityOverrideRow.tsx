import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import type { KeyboardEvent, ReactNode } from "react";

// ---------------------------------------------------------------------------
// CapabilityOverrideRow: three-state inline video capability overrides, initially last_frame only.
//
// States directly match sparse-override writes, without fourth intermediate states:
//   follow → key absent, effective values follow system decisions and their changes
//   on/off → { last_frame: true|false } permanently overrides system decisions
// Follow embeds system decisions within segments, letting users see decisions and choose overrides
// in the same controls, without extra explanation rows.
// ---------------------------------------------------------------------------

export type OverrideState = "follow" | "on" | "off";

/** Segment display order, also keyboard left/right order. */
const SEGMENT_ORDER: OverrideState[] = ["follow", "on", "off"];

/** Bidirectional mapping of override values (undefined = absent) and states, sole write conversion point. */
function overrideToState(override: boolean | undefined): OverrideState {
  if (override === undefined) return "follow";
  return override ? "on" : "off";
}

function stateToOverride(state: OverrideState): boolean | undefined {
  if (state === "follow") return undefined;
  return state === "on";
}

interface CapabilityOverrideRowProps {
  /** Current overrides; undefined follows system decisions. */
  override: boolean | undefined;
  /** System decisions; null before evaluation (new/changed model_id rows, calculated after server saves). */
  systemValue: boolean | null;
  /** Whether endpoint execution forwards ending-frame constraints; false disables force-on, otherwise backends reject with 422. */
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
  // Skip unavailable force-on segments when endpoints omit ending frames; never trap keyboard focus on unselectable segments.
  const reachable = SEGMENT_ORDER.filter((s) => s !== "on" || endImageCapable);
  // Roving tabindex: groups occupy one Tab stop, arrows navigate internally (matching ModelConfigSection duration
  // selectors). Unreachable current states (theoretically absent) use first reachable segments, preventing all -1 inaccessible groups.
  const tabbable = reachable.includes(state) ? state : reachable[0];

  const select = (target: OverrideState) => {
    onChange(stateToOverride(target));
    groupRef.current?.querySelector<HTMLButtonElement>(`[data-state="${target}"]`)?.focus();
  };

  // Standard radio keyboard behavior: arrows focus/select, Home/End jump boundaries. Attach per segment, never containers,
  // since focus already sits in segments and containers must not focus.
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
        className={cn(
          "-ml-px h-8 border px-2.5 text-sm transition-colors first:ml-0 first:rounded-l-lg last:rounded-r-lg focus-visible:relative focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50",
          active ? "relative border-primary/40 bg-primary/15 text-foreground" : "border-input text-muted-foreground hover:text-foreground",
        )}
      >
        {label}
      </button>
    );
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <div ref={groupRef} className="flex items-center" role="radiogroup" aria-label={t("cap_override_group_label")}>
          {segment(
            "follow",
            <span>
              {t("cap_override_follow")}
              <span className="text-muted-foreground"> · {detectedLabel}</span>
            </span>,
            // Accessible names include decisions, matching screen-reader/visual information.
            `${t("cap_override_follow")}·${detectedLabel}`,
            t("cap_override_follow_title"),
          )}
          {segment("on", t("cap_override_on"), t("cap_override_on"), t("cap_override_on_title"))}
          {segment("off", t("cap_override_off"), t("cap_override_off"), t("cap_override_off_title"))}
        </div>

        {overridden && (
          <Badge variant="secondary">
            {t("cap_override_badge")}
          </Badge>
        )}

        {overridden && (
          <span className="text-xs text-muted-foreground">
            {t("cap_override_effective", {
              effective: valueLabel(state === "on"),
              detected: detectedLabel,
            })}
          </span>
        )}
      </div>

      {/* title is inaccessible to keyboards/touch; disabled reasons require visible explanatory rows. */}
      {!endImageCapable && <p className="text-xs text-muted-foreground">{t("cap_override_on_unavailable")}</p>}
    </div>
  );
}
