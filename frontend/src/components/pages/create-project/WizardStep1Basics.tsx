import { useState } from "react";
import { useTranslation } from "react-i18next";

export interface WizardStep1Value {
  title: string;
  contentMode: "narration" | "drama";
  aspectRatio: "9:16" | "16:9";
  generationMode: "single" | "grid";
}

export interface WizardStep1BasicsProps {
  value: WizardStep1Value;
  onChange: (next: WizardStep1Value) => void;
  onNext: () => void;
  onCancel: () => void;
}

export function WizardStep1Basics({
  value,
  onChange,
  onNext,
  onCancel,
}: WizardStep1BasicsProps) {
  const { t } = useTranslation(["common", "dashboard", "templates"]);
  const [titleError, setTitleError] = useState("");

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTitleError("");
    onChange({ ...value, title: e.target.value });
  };

  const handleNext = () => {
    if (!value.title.trim()) {
      setTitleError(t("dashboard:project_title_required"));
      return;
    }
    onNext();
  };

  const radioClass = (selected: boolean) =>
    `flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors ${
      selected
        ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
        : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
    }`;

  return (
    <div className="space-y-4">
      {/* Title */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          {t("dashboard:project_title")}{" "}
          {!value.title.trim() && <span className="text-red-400">*</span>}
        </label>
        <input
          type="text"
          value={value.title}
          onChange={handleTitleChange}
          placeholder={t("dashboard:rebirth_empress_example")}
          className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
        />
        {titleError && (
          <p className="mt-1 text-xs text-red-400">{titleError}</p>
        )}
        <p className="mt-1 text-xs text-gray-600">
          {t("dashboard:project_id_auto_gen_hint")}
        </p>
      </div>

      {/* Content Mode */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          {t("dashboard:content_mode")}
        </label>
        <div className="flex gap-3">
          <label className={radioClass(value.contentMode === "narration")}>
            <input
              type="radio"
              name="contentMode"
              value="narration"
              checked={value.contentMode === "narration"}
              onChange={() => onChange({ ...value, contentMode: "narration" })}
              className="sr-only"
            />
            {t("dashboard:narration_visuals")}
          </label>
          <label className={radioClass(value.contentMode === "drama")}>
            <input
              type="radio"
              name="contentMode"
              value="drama"
              checked={value.contentMode === "drama"}
              onChange={() => onChange({ ...value, contentMode: "drama" })}
              className="sr-only"
            />
            {t("dashboard:drama_animation")}
          </label>
        </div>
      </div>

      {/* Aspect Ratio */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          {t("dashboard:aspect_ratio")}
        </label>
        <div className="flex gap-3">
          <label className={radioClass(value.aspectRatio === "9:16")}>
            <input
              type="radio"
              name="aspectRatio"
              value="9:16"
              checked={value.aspectRatio === "9:16"}
              onChange={() => onChange({ ...value, aspectRatio: "9:16" })}
              className="sr-only"
            />
            {t("dashboard:portrait_9_16")}
          </label>
          <label className={radioClass(value.aspectRatio === "16:9")}>
            <input
              type="radio"
              name="aspectRatio"
              value="16:9"
              checked={value.aspectRatio === "16:9"}
              onChange={() => onChange({ ...value, aspectRatio: "16:9" })}
              className="sr-only"
            />
            {t("dashboard:landscape_16_9")}
          </label>
        </div>
      </div>

      {/* Generation Mode */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-0.5">
          {t("dashboard:generation_mode")}
        </label>
        <p className="text-xs text-gray-600 mb-1.5">
          {t("dashboard:generation_mode_desc")}
        </p>
        <div className="flex gap-3">
          <label className={radioClass(value.generationMode === "single")}>
            <input
              type="radio"
              name="generationMode"
              value="single"
              checked={value.generationMode === "single"}
              onChange={() => onChange({ ...value, generationMode: "single" })}
              className="sr-only"
            />
            {t("dashboard:single_generation")}
          </label>
          <label className={radioClass(value.generationMode === "grid")}>
            <input
              type="radio"
              name="generationMode"
              value="grid"
              checked={value.generationMode === "grid"}
              onChange={() => onChange({ ...value, generationMode: "grid" })}
              className="sr-only"
            />
            {t("dashboard:grid_generation")}
          </label>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          {t("common:cancel")}
        </button>
        <button
          type="button"
          onClick={handleNext}
          disabled={!value.title.trim()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {t("templates:next_step")}
        </button>
      </div>
    </div>
  );
}
