import type { TFunction } from "i18next";
import type { ReferenceVideoBucket, VideoCapabilityProblem } from "@/types";

/** 单元所落桶的用户可读名，随问题文案插值。 */
export function bucketLabel(t: TFunction<"dashboard">, bucket: ReferenceVideoBucket): string {
  return t(bucket === "r2v" ? "reference_bucket_r2v" : "reference_bucket_i2v");
}

function tierProblemReasonKey(code: string): string {
  if (code.startsWith("video_capability_missing_")) return "reference_unit_tier_reason_unsupported";
  switch (code) {
    case "video_capability_reference_unavailable":
    case "reference_capability_unavailable":
      return "reference_unit_tier_reason_removed";
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

/**
 * 所落桶的视频请求事实失败 → 「档位未知」标签与带问题码、成因、修复指引的提示。
 * 成因文案按桶插值：i2v 桶失败说图生视频模型，r2v 桶失败说参考生视频模型，不混用。
 */
export function tierProblemText(
  t: TFunction<"dashboard">,
  problem: VideoCapabilityProblem,
  bucket: ReferenceVideoBucket,
): { label: string; hint: string } {
  const bucketText = bucketLabel(t, bucket);
  return {
    label: t("reference_unit_tier_unknown_label", { bucket: bucketText }),
    hint: t("reference_unit_tier_unknown_hint", {
      code: problem.code,
      bucket: bucketText,
      reason: t(tierProblemReasonKey(problem.code), { bucket: bucketText }),
    }),
  };
}
