import type { TFunction } from "i18next";
import { DurationParseError, parseDurationInput, type DurationParseErrorCode } from "@/utils/duration_format";

/** 支持秒数解析错误码对应的提示文案。 */
export const DURATION_ERROR_KEY: Record<DurationParseErrorCode, string> = {
  empty_after_split: "supported_durations_err_empty_after_split",
  non_positive: "supported_durations_err_non_positive",
  exceeds_max: "supported_durations_err_exceeds_max",
  range_too_large: "supported_durations_err_range_too_large",
  range_inverted: "supported_durations_err_range_inverted",
  unparseable: "supported_durations_err_unparseable",
};

/** 支持秒数的格式错误；空值或格式正确时为 null。 */
export function durationsError(t: TFunction, text: string): string | null {
  if (!text.trim()) return null;
  try {
    parseDurationInput(text);
    return null;
  } catch (e) {
    if (e instanceof DurationParseError) return t(DURATION_ERROR_KEY[e.code], e.params);
    return t(DURATION_ERROR_KEY.unparseable, { seg: "" });
  }
}
