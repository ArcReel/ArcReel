import type { TFunction } from "i18next";
import type { DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";

const INCOMPATIBLE_KEY: Record<DurationOutOfRangeReason, string> = {
  model: "duration_incompatible_warning",
  resolution: "duration_incompatible_resolution_warning",
  reference: "duration_incompatible_reference_warning",
};

/**
 * Explain unsupported duration tiers by cause: say the model does not support a value only when absent from its complete tier set; when resolution/reference
 * constraints narrow it out, identify the constraint so users can change the corresponding setting.
 */
export function durationIncompatibleLabel(
  t: TFunction,
  seconds: number,
  options: readonly number[],
  reason: DurationOutOfRangeReason | null | undefined,
): string {
  return t(INCOMPATIBLE_KEY[reason ?? "model"], { value: seconds, supported: options.join(", ") });
}
