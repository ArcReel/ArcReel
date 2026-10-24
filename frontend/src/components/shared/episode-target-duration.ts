/** 硬区间（闭区间，秒）：与后端 lib.episode.episode_target_duration 的 is_valid_episode_target_duration 同一把尺。 */
export const EPISODE_TARGET_DURATION_MIN = 10;
export const EPISODE_TARGET_DURATION_MAX = 600;

/** 该值是否可提交（null = 未设目标，合法）。 */
export function isValidEpisodeTargetDuration(value: number | null): boolean {
  if (value === null) return true;
  return (
    Number.isInteger(value) &&
    value >= EPISODE_TARGET_DURATION_MIN &&
    value <= EPISODE_TARGET_DURATION_MAX
  );
}
