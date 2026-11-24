import type { TFunction } from "i18next";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";

const INCOMPATIBLE_KEY: Record<DurationOutOfRangeReason, string> = {
  model: "duration_incompatible_warning",
  resolution: "duration_incompatible_resolution_warning",
  reference: "duration_incompatible_reference_warning",
  end_frame: "duration_incompatible_end_frame_warning",
};

const INCOMPATIBLE_EMPTY_KEY: Record<DurationOutOfRangeReason, string> = {
  model: "duration_incompatible_empty_warning",
  resolution: "duration_incompatible_resolution_empty_warning",
  reference: "duration_incompatible_reference_empty_warning",
  end_frame: "duration_incompatible_end_frame_empty_warning",
};

export function durationIncompatibleLabel(
  t: TFunction,
  seconds: number,
  options: readonly number[],
  reason: DurationOutOfRangeReason | null | undefined,
): string {
  const table = options.length > 0 ? INCOMPATIBLE_KEY : INCOMPATIBLE_EMPTY_KEY;
  return t(table[reason ?? "model"], { value: seconds, supported: options.join(", ") });
}
