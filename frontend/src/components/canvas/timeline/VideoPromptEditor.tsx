import { useTranslation } from "react-i18next";
import { Textarea } from "@/components/ui/textarea";
import { CAMERA_MOTIONS, CAMERA_MOTION_I18N_KEYS } from "@/types";
import type { VideoPrompt } from "@/types";
import { CompactInput } from "./CompactInput";
import { PromptFieldGrid } from "./PromptFieldGrid";
import { PromptFieldSelect } from "./PromptFieldSelect";

interface VideoPromptEditorProps {
  prompt: VideoPrompt;
  onUpdate: (patch: Partial<VideoPrompt>) => void;
  /** 只读展示（引导演示项目）：字段可读不可改。 */
  readOnly?: boolean;
}

/** 结构化的视频提示词：动作描述加可折叠的镜头运动与环境音效。台词在分镜详情的台词区单独编辑。 */
export function VideoPromptEditor({ prompt, onUpdate, readOnly }: VideoPromptEditorProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex flex-col gap-2">
      <Textarea
        value={prompt.action}
        onChange={(e) => onUpdate({ action: e.target.value })}
        readOnly={readOnly}
        aria-label={t("detail_video_prompt_title")}
        placeholder={t("video_prompt_placeholder")}
        className="max-h-none"
      />
      <PromptFieldGrid title={t("camera_motion_section")}>
        <PromptFieldSelect
          label={t("camera_motion_label")}
          value={prompt.camera_motion}
          options={CAMERA_MOTIONS}
          renderOption={(value) => t(CAMERA_MOTION_I18N_KEYS[value])}
          disabled={readOnly}
          onChange={(camera_motion) => onUpdate({ camera_motion })}
        />
        <CompactInput
          label={t("ambiance_audio_label")}
          value={prompt.ambiance_audio}
          onChange={(ambiance_audio) => onUpdate({ ambiance_audio })}
          readOnly={readOnly}
          placeholder={t("ambiance_audio_placeholder")}
        />
      </PromptFieldGrid>
    </div>
  );
}
