
import { useState } from "react";
import { useLocation } from "wouter";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { DEFAULT_TEMPLATE_ID } from "@/data/style-templates";
import { WizardStep1Basics, type WizardStep1Value } from "./create-project/WizardStep1Basics";
import { WizardStep2Models } from "./create-project/WizardStep2Models";
import { WizardStep3Style, type WizardStep3Value } from "./create-project/WizardStep3Style";
import type { ModelConfigValue } from "@/components/shared/ModelConfigSection";

// ─── Step indicator ───────────────────────────────────────────────────────────

const STEPS = [
  { num: 1, key: "wizard_step_basics" },
  { num: 2, key: "wizard_step_models" },
  { num: 3, key: "wizard_step_style" },
] as const;

function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  const { t } = useTranslation("templates");
  return (
    <div className="flex items-center justify-center gap-2">
      {STEPS.map((s, i) => {
        const done = current > s.num;
        const active = current === s.num;
        return (
          <div key={s.num} className="flex items-center gap-2">
            <div className="flex items-center gap-2">
              <div
                className={
                  done
                    ? "w-6 h-6 rounded-full bg-indigo-500 text-white flex items-center justify-center text-xs font-semibold"
                    : active
                      ? "w-6 h-6 rounded-full bg-indigo-500/15 border-[1.5px] border-indigo-500 text-indigo-300 flex items-center justify-center text-xs font-semibold"
                      : "w-6 h-6 rounded-full bg-gray-900 border-[1.5px] border-gray-700 text-gray-500 flex items-center justify-center text-xs font-semibold"
                }
              >
                {done ? "✓" : s.num}
              </div>
              <span
                className={
                  done
                    ? "text-xs text-indigo-300"
                    : active
                      ? "text-xs text-gray-100 font-medium"
                      : "text-xs text-gray-500"
                }
              >
                {t(s.key)}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={`w-8 h-px ${current > s.num ? "bg-indigo-500" : "bg-gray-700"}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CreateProjectModal() {
  const { t } = useTranslation(["dashboard"]);
  const [, navigate] = useLocation();
  const { setShowCreateModal } = useProjectsStore();

  const [step, setStep] = useState<1 | 2 | 3>(1);

  const [basics, setBasics] = useState<WizardStep1Value>({
    title: "",
    contentMode: "narration",
    aspectRatio: "9:16",
    generationMode: "single",
  });

  const [models, setModels] = useState<ModelConfigValue>({
    videoBackend: "",
    imageBackend: "",
    textBackendScript: "",
    textBackendOverview: "",
    textBackendStyle: "",
    defaultDuration: null,
  });

  const [style, setStyle] = useState<WizardStep3Value>({
    mode: "template",
    templateId: DEFAULT_TEMPLATE_ID,
    activeCategory: "live",
    uploadedFile: null,
    uploadedPreview: null,
  });

  const [creating, setCreating] = useState(false);

  const handleClose = () => {
    if (style.uploadedPreview) URL.revokeObjectURL(style.uploadedPreview);
    setShowCreateModal(false);
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const resp = await API.createProject({
        title: basics.title.trim(),
        content_mode: basics.contentMode,
        aspect_ratio: basics.aspectRatio,
        generation_mode: basics.generationMode,
        default_duration: models.defaultDuration,
        style_template_id: style.mode === "template" ? style.templateId : null,
        video_backend: models.videoBackend || null,
        image_backend: models.imageBackend || null,
        text_backend_script: models.textBackendScript || null,
        text_backend_overview: models.textBackendOverview || null,
        text_backend_style: models.textBackendStyle || null,
      });

      // Upload style image if in custom mode
      if (style.mode === "custom" && style.uploadedFile) {
        try {
          await API.uploadStyleImage(resp.name, style.uploadedFile);
        } catch {
          useAppStore.getState().pushToast(
            t("dashboard:style_upload_failed_hint"),
            "warning"
          );
        }
      }

      // Revoke any object URL before closing
      if (style.uploadedPreview) {
        URL.revokeObjectURL(style.uploadedPreview);
      }

      setShowCreateModal(false);
      navigate(`/app/projects/${resp.name}`);
    } catch (err) {
      useAppStore.getState().pushToast(
        `${t("dashboard:create_project_failed")}${(err as Error).message}`,
        "error"
      );
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-3xl rounded-xl border border-gray-700 bg-gray-900 p-6 shadow-2xl max-h-[90vh] overflow-y-auto">
        {/* Header: title + close */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-gray-100">{t("dashboard:new_project")}</h2>
          <button
            type="button"
            onClick={handleClose}
            className="rounded p-1 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Step indicator */}
        <StepIndicator current={step} />

        {/* Current step */}
        <div className="mt-6">
          {step === 1 && (
            <WizardStep1Basics
              value={basics}
              onChange={setBasics}
              onNext={() => setStep(2)}
              onCancel={handleClose}
            />
          )}
          {step === 2 && (
            <WizardStep2Models
              value={models}
              onChange={setModels}
              onBack={() => setStep(1)}
              onNext={() => setStep(3)}
              onCancel={handleClose}
            />
          )}
          {step === 3 && (
            <WizardStep3Style
              value={style}
              onChange={setStyle}
              onBack={() => setStep(2)}
              onCreate={handleCreate}
              onCancel={handleClose}
              creating={creating}
            />
          )}
        </div>
      </div>
    </div>
  );
}
