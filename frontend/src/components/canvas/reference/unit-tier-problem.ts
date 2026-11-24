import type { TFunction } from "i18next";
import type { ReferenceVideoBucket, VideoCapabilityProblem } from "@/types";

export function bucketLabel(t: TFunction<"dashboard">, bucket: ReferenceVideoBucket): string {
  return t(bucket === "r2v" ? "reference_bucket_r2v" : "reference_bucket_i2v");
}

function tierProblemReasonKey(code: string): string {
  if (code.startsWith("video_capability_missing_")) return "reference_unit_tier_reason_unsupported";
  switch (code) {
    case "video_capability_reference_unavailable":
    case "model_not_in_provider_catalog":
    case "reference_capability_unavailable":
      return "reference_unit_tier_reason_removed";
    case "model_not_selected":
      return "reference_unit_tier_reason_not_selected";
    case "model_discontinued":
      return "reference_unit_tier_reason_discontinued";
    case "reference_supported_durations_missing":
      return "reference_unit_tier_reason_missing_tiers";
    case "reference_supported_durations_invalid":
      return "reference_unit_tier_reason_invalid_tiers";
    case "reference_supported_durations_incompatible":
      return "reference_unit_tier_reason_incompatible";
    default:
      return "reference_unit_tier_reason_unresolved";
  }
}

export function tierProblemText(
  t: TFunction<"dashboard">,
  problem: VideoCapabilityProblem,
  bucket: ReferenceVideoBucket,
): { label: string; hint: string } {
  const bucketText = bucketLabel(t, bucket);
  return {
    label: t("reference_unit_tier_unknown_label", { bucket: bucketText }),
    hint: t("reference_unit_tier_unknown_hint", {
      bucket: bucketText,
      reason: t(tierProblemReasonKey(problem.code), { bucket: bucketText }),
    }),
  };
}
