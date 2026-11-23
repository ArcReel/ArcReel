import { useTranslation } from "react-i18next";
import { OptionalNumberField } from "@/components/shared/OptionalNumberField";
import {
  EPISODE_TARGET_DURATION_MAX,
  EPISODE_TARGET_DURATION_MIN,
  isValidEpisodeTargetDuration,
} from "./episode-target-duration";


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
