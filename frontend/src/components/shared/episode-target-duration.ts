/** Hard interval (inclusive, in seconds): uses the same criteria as is_valid_episode_target_duration in backend lib.episode.episode_target_duration. */
export const EPISODE_TARGET_DURATION_MIN = 10;
export const EPISODE_TARGET_DURATION_MAX = 600;

/** Whether this value can be submitted (null means no target is set, which is valid). */
export function isValidEpisodeTargetDuration(value: number | null): boolean {
  if (value === null) return true;
  return (
    Number.isInteger(value) &&
    value >= EPISODE_TARGET_DURATION_MIN &&
    value <= EPISODE_TARGET_DURATION_MAX
  );
}
