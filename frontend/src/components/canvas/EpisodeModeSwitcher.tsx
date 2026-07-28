import { useTranslation } from "react-i18next";
import { GenerationModeSelector } from "@/components/shared/GenerationModeSelector";
import { normalizeMode, type GenerationMode } from "@/utils/generation-mode";
import { useAppStore } from "@/stores/app-store";

export interface EpisodeModeSwitcherProps {
  /** Project-level mode, used as fallback when episode has no override. */
  projectMode: GenerationMode;
  /** Current episode-level override; undefined = inherit from project. */
  episodeMode: GenerationMode | undefined;
  /** Called with the new mode. Parent should PATCH the episode override. */
  onChange: (next: GenerationMode) => void;
}

export function EpisodeModeSwitcher({ projectMode, episodeMode, onChange }: EpisodeModeSwitcherProps) {
  const { t } = useTranslation("dashboard");
  const effective = normalizeMode(episodeMode ?? projectMode);

  // 切换生成模式不清空旧数据；toast 按 from→to 三种文案之一告知用户"旧数据保留"。
  const handleChange = (next: GenerationMode) => {
    if (next === effective) return;
    onChange(next);

    const toastKey =
      next === "reference_video"
        ? "episode_mode_switch_to_reference"
        : effective === "reference_video"
          ? "episode_mode_switch_from_reference"
          : "episode_mode_switch_keep_data";
    useAppStore.getState().pushToast(t(toastKey), "info");
  };

  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span>{t("episode_mode_switcher_label")}:</span>
      <GenerationModeSelector
        value={effective}
        onChange={handleChange}
        size="sm"
        name="episodeMode"
      />
      {episodeMode === undefined && (
        <span className="text-gray-600">({t("episode_mode_inherit_from_project")})</span>
      )}
    </div>
  );
}
