import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { CompactInput } from "@/components/ui/CompactInput";
import { DropdownPill } from "@/components/ui/DropdownPill";
import { SHOT_TYPES } from "@/types";
import type { ImagePrompt, ShotType } from "@/types";

interface ImagePromptEditorProps {
  prompt: ImagePrompt;
  onUpdate: (patch: Partial<ImagePrompt>) => void;
}

/** Structured editor for ImagePrompt fields with collapsible composition section. */
export function ImagePromptEditor({
  prompt,
  onUpdate,
}: ImagePromptEditorProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <AutoTextarea
        value={prompt.scene}
        onChange={(v) => onUpdate({ scene: v })}
        placeholder="Storyboard description..."
      />

      {/* Collapsible composition fields */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="inline-flex items-center gap-1 self-start text-[10px] text-gray-500 hover:text-gray-400"
      >
        <ChevronDown
          className={`h-3 w-3 transition-transform ${collapsed ? "-rotate-90" : ""}`}
        />
        Composition params
      </button>

      {!collapsed && (
        <div className="flex flex-col gap-2 pl-1">
          <DropdownPill
            label="Shot"
            value={prompt.composition.shot_type}
            options={SHOT_TYPES}
            onChange={(v: ShotType) =>
              onUpdate({
                composition: { ...prompt.composition, shot_type: v },
              })
            }
          />
          <CompactInput
            label="Lighting"
            value={prompt.composition.lighting}
            onChange={(v) =>
              onUpdate({
                composition: { ...prompt.composition, lighting: v },
              })
            }
            placeholder="Lighting description..."
          />
          <CompactInput
            label="Ambiance"
            value={prompt.composition.ambiance}
            onChange={(v) =>
              onUpdate({
                composition: { ...prompt.composition, ambiance: v },
              })
            }
            placeholder="Ambiance description..."
          />
        </div>
      )}
    </div>
  );
}
