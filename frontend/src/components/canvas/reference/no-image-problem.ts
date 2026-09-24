export function noImageReasonKey(code: string): string {
  switch (code) {
    case "video_capability_missing_i2v": return "reference_no_image_reason_unsupported";
    case "video_capability_reference_unavailable": return "reference_no_image_reason_removed";
    case "reference_supported_durations_missing": return "reference_no_image_reason_missing_tiers";
    case "reference_supported_durations_invalid": return "reference_no_image_reason_invalid_tiers";
    case "reference_supported_durations_incompatible": return "reference_no_image_reason_incompatible";
    default: return "reference_no_image_reason_unresolved";
  }
}
