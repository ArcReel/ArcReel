import { useTranslation } from "react-i18next";
import { Textarea } from "@/components/ui/textarea";
import { SHOT_TYPES, SHOT_TYPE_I18N_KEYS } from "@/types";
import type { ImagePrompt } from "@/types";
import { CompactInput } from "./CompactInput";
import { PromptFieldGrid } from "./PromptFieldGrid";
import { PromptFieldSelect } from "./PromptFieldSelect";

interface ImagePromptEditorProps {
  prompt: ImagePrompt;
  onUpdate: (patch: Partial<ImagePrompt>) => void;
  /** 只读展示（引导演示项目）：字段可读不可改。 */
  readOnly?: boolean;
}

/** 结构化的分镜图提示词：画面描述加可折叠的构图参数（景别、光线、氛围）。 */
export function ImagePromptEditor({ prompt, onUpdate, readOnly }: ImagePromptEditorProps) {
  const { t } = useTranslation("dashboard");
  const updateComposition = (patch: Partial<ImagePrompt["composition"]>) =>
    onUpdate({ composition: { ...prompt.composition, ...patch } });

  return (
    <div className="flex flex-col gap-2">
      <Textarea
        value={prompt.scene}
        onChange={(e) => onUpdate({ scene: e.target.value })}
        readOnly={readOnly}
        aria-label={t("detail_image_prompt_title")}
        placeholder={t("image_prompt_placeholder")}
        className="max-h-none"
      />
      <PromptFieldGrid title={t("composition_params")}>
        <PromptFieldSelect
          label={t("shot_label")}
          value={prompt.composition.shot_type}
          options={SHOT_TYPES}
          renderOption={(value) => t(SHOT_TYPE_I18N_KEYS[value])}
          disabled={readOnly}
          onChange={(shot_type) => updateComposition({ shot_type })}
        />
        <CompactInput
          label={t("lighting_label")}
          value={prompt.composition.lighting}
          onChange={(lighting) => updateComposition({ lighting })}
          readOnly={readOnly}
          placeholder={t("lighting_placeholder")}
        />
        <CompactInput
          label={t("ambiance_label")}
          value={prompt.composition.ambiance}
          onChange={(ambiance) => updateComposition({ ambiance })}
          readOnly={readOnly}
          placeholder={t("ambiance_placeholder")}
        />
      </PromptFieldGrid>
    </div>
  );
}
