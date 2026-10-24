import { useTranslation } from "react-i18next";
import { OptionalNumberField } from "@/components/shared/OptionalNumberField";
import {
  EPISODE_TARGET_DURATION_MAX,
  EPISODE_TARGET_DURATION_MIN,
  isValidEpisodeTargetDuration,
} from "./episode-target-duration";

/**
 * 单集目标时长（秒）的项目级可选输入。
 *
 * 与「默认时长」是两个尺度：那个是单个分镜 / 视频单元的默认秒数（受视频模型档位约束，
 * 落在模型配置栏内），这个是整集成片的期望体量，驱动脚本规划决定本集拆多少个单元。
 * 两者同为软偏好，可被内容需要覆盖；超出目标只提示、不阻断确认与后续生成。
 *
 * 广告/短片项目不呈现该输入：整集体量已由目标总时长（target_duration）预算表达，
 * 两套总量口径并存会互相竞争。
 */

export interface EpisodeTargetDurationFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
}

export function EpisodeTargetDurationField({ value, onChange }: EpisodeTargetDurationFieldProps) {
  const { t } = useTranslation("dashboard");

  return (
    <OptionalNumberField
      label={t("episode_target_duration_label")}
      value={value}
      onChange={onChange}
      unit={t("episode_target_duration_unit")}
      hint={t("episode_target_duration_hint")}
      errorMessage={t("episode_target_duration_out_of_range", {
        min: EPISODE_TARGET_DURATION_MIN,
        max: EPISODE_TARGET_DURATION_MAX,
      })}
      invalid={!isValidEpisodeTargetDuration(value)}
      idSuffix="episode-target-duration"
      inputMode="numeric"
      max={EPISODE_TARGET_DURATION_MAX}
      step={1}
    />
  );
}
