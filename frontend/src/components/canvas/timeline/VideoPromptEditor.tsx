import { useTranslation } from "react-i18next";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { CompactInput } from "@/components/ui/CompactInput";
import type { VideoPrompt } from "@/types";

interface VideoPromptEditorProps {
  prompt: VideoPrompt;
  onUpdate: (patch: Partial<VideoPrompt>) => void;
  /** 只读展示（引导演示项目）：字段可读不可改。 */
  readOnly?: boolean;
}

/** Structured editor for VideoPrompt — action 已融合运镜描述，不再有独立 Camera_Motion 字段。 */
export function VideoPromptEditor({
  prompt,
  onUpdate,
  readOnly,
}: VideoPromptEditorProps) {
  const { t } = useTranslation("dashboard");

  return (
    <div className="flex flex-col gap-2">
      <AutoTextarea
        value={prompt.action}
        onChange={(v) => onUpdate({ action: v })}
        readOnly={readOnly}
        placeholder={t("video_prompt_placeholder")}
      />

      <CompactInput
        label={t("ambiance_audio_label")}
        value={prompt.ambiance_audio}
        onChange={(v) => onUpdate({ ambiance_audio: v })}
        readOnly={readOnly}
        placeholder={t("ambiance_audio_placeholder")}
      />
    </div>
  );
}
