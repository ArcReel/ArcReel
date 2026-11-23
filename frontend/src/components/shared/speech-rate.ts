export const SPEECH_RATE_MIN = 0.001;
export const SPEECH_RATE_MAX = 20;

export function isValidSpeechRate(value: number | null): boolean {
  if (value === null) return true;
  return value >= SPEECH_RATE_MIN && value <= SPEECH_RATE_MAX;
}
