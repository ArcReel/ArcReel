/**
 * Hard interval (inclusive): matches backend lib.speech.speech_rate.is_valid_speech_rate.
 * For the lower bound rationale, including headroom for downstream duration conversion, see backend MIN_SPEECH_RATE_UPS comments.
 */
export const SPEECH_RATE_MIN = 0.001;
export const SPEECH_RATE_MAX = 20;

/** Whether this value can be submitted (null means omitted, which is valid). */
export function isValidSpeechRate(value: number | null): boolean {
  if (value === null) return true;
  return value >= SPEECH_RATE_MIN && value <= SPEECH_RATE_MAX;
}
