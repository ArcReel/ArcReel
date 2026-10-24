import type { TFunction } from "i18next";
import type { CapabilityKey, CapabilityOverrides } from "@/types";
import { compactRangeFormat } from "@/utils/duration_format";

export type Constraints = NonNullable<CapabilityOverrides["duration_constraints"]>;

export const BOOL_KEYS = new Set<CapabilityKey>([
  "image_input",
  "structured_output",
  "text_to_image",
  "image_to_image",
  "text_to_video",
  "first_frame",
  "last_frame",
]);

export const ENUM_OPTIONS: Partial<Record<CapabilityKey, string[]>> = {
  reference_audio_mode: ["none", "direct"],
  audio_track: ["controllable", "always_on", "always_off"],
  reference_route_audio_track: ["controllable", "always_on", "always_off"],
};

export function asConstraints(value: unknown): Constraints {
  return value && typeof value === "object" ? (value) : {};
}

/** 时长联动约束的分辨率键与可选分辨率按小写比较（与服务端一致）。 */
export function sameResolution(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** 去掉时长联动约束里已不在可选分辨率中的分辨率。 */
export function withoutDroppedResolutions(constraints: unknown, resolutions: string[]): Constraints {
  const value = asConstraints(constraints);
  const byResolution = Object.fromEntries(
    Object.entries(value.by_resolution ?? {}).filter(([res]) => resolutions.some((kept) => sameResolution(kept, res))),
  );
  return { ...value, by_resolution: byResolution };
}

export function scalarText(value: unknown): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : JSON.stringify(value);
}

/** 维度取值的文字：原值与修改后的值共用。`null` 表示不加约束。 */
export function capabilityValueText(t: TFunction, key: CapabilityKey, value: unknown): string {
  if (value === null || value === undefined) return t("pm_cap_value_unlimited");
  if (BOOL_KEYS.has(key)) return t(value ? "pm_cap_value_supported" : "pm_cap_value_unsupported");
  if (key === "durations") {
    const list = value as number[];
    return list.length ? t("pm_cap_value_seconds", { list: compactRangeFormat(list) }) : t("pm_cap_value_none");
  }
  if (key === "resolutions") {
    const list = value as string[];
    return list.length ? list.join(t("pm_cap_value_list_separator")) : t("pm_cap_value_none");
  }
  if (key === "duration_constraints") {
    const constraints = asConstraints(value);
    const parts = Object.entries(constraints.by_resolution ?? {}).map(([res, allowed]) =>
      t("pm_cap_constraint_resolution_item", { resolution: res, list: compactRangeFormat(allowed) }),
    );
    if (constraints.with_reference_images?.length) {
      parts.push(t("pm_cap_constraint_reference_item", { list: compactRangeFormat(constraints.with_reference_images) }));
    }
    return parts.length ? parts.join(t("pm_cap_value_group_separator")) : t("pm_cap_value_none");
  }
  if (ENUM_OPTIONS[key]) return t(`pm_cap_option_${key === "reference_audio_mode" ? "audio_mode" : "audio_track"}_${scalarText(value)}`);
  if (key === "max_reference_audio_total_seconds") return t("pm_cap_value_seconds", { list: scalarText(value) });
  return scalarText(value);
}
