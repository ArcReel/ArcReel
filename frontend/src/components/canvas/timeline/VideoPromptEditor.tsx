import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { CompactInput } from "@/components/ui/CompactInput";
import { DropdownPill } from "@/components/ui/DropdownPill";
import { DialogueListEditor } from "./DialogueListEditor";
import { CAMERA_MOTIONS, CAMERA_MOTION_I18N_KEYS } from "@/types";
import type { VideoPrompt, CameraMotion, Dialogue } from "@/types";

interface VideoPromptEditorProps {
  prompt: VideoPrompt;
  onUpdate: (patch: Partial<VideoPrompt>) => void;
}

/** Structured editor for VideoPrompt fields with collapsible metadata section. */
export function VideoPromptEditor({
  prompt,
  onUpdate,
}: VideoPromptEditorProps) {
  const { t } = useTranslation("dashboard");
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <AutoTextarea
        value={prompt.action}
        onChange={(v) => onUpdate({ action: v })}
        placeholder={t("video_prompt_placeholder")}
      />

      {/* Collapsible metadata fields */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="inline-flex items-center gap-1 self-start text-[10px] text-gray-500 hover:text-gray-400"
      >
        <ChevronDown
          className={`h-3 w-3 transition-transform ${collapsed ? "-rotate-90" : ""}`}
        />
        {t("camera_motion_section")}
      </button>

      {!collapsed && (
        <div className="flex flex-col gap-2 pl-1">
          <DropdownPill
            label={t("camera_motion_label")}
            value={prompt.camera_motion}
            options={CAMERA_MOTIONS}
            renderOption={(v: CameraMotion) => t(CAMERA_MOTION_I18N_KEYS[v])}
            onChange={(v: CameraMotion) => onUpdate({ camera_motion: v })}
          />
          <CompactInput
            label={t("ambiance_audio_label")}
            value={prompt.ambiance_audio}
            onChange={(v) => onUpdate({ ambiance_audio: v })}
            placeholder={t("ambiance_audio_placeholder")}
          />
          <DialogueListEditor
            dialogue={prompt.dialogue ?? []}
            onChange={(d: Dialogue[]) => onUpdate({ dialogue: d })}
          />
        </div>
      )}
    </div>
  );
}
