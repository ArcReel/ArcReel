export const EPISODE_TARGET_DURATION_MIN = 10;
export const EPISODE_TARGET_DURATION_MAX = 600;

export function isValidEpisodeTargetDuration(value: number | null): boolean {
  if (value === null) return true;
  return (
    Number.isInteger(value) &&
    value >= EPISODE_TARGET_DURATION_MIN &&
    value <= EPISODE_TARGET_DURATION_MAX
  );
}
