import { useEffect, useId, useRef, useState } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { DEFAULT_TEMPLATE_ID } from "@/data/style-templates";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
import { formatNameList } from "@/utils/list-format";
import { catalogDisplayNames, catalogDurations } from "@/utils/provider-models";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { isValidEpisodeTargetDuration } from "@/components/shared/episode-target-duration";
import { isValidSpeechRate } from "@/components/shared/speech-rate";
import { executingImageModel, executingVideoModel } from "@/components/shared/model-layers";
import type { ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { narrationDeliveryProblem, type NarrationDeliveryValue } from "@/components/shared/narration-delivery";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { WizardStepBasics, type WizardBasicsValue } from "./create-project/WizardStepBasics";
import {
  WizardStepGeneration,
  type WizardDurationValue,
  type WizardGenerationData,
} from "./create-project/WizardStepGeneration";
import { WizardStepper, type WizardStep } from "./create-project/WizardStepper";

const EMPTY_MODELS: ModelConfigValue = {
  videoBackend: "",
  videoProviderI2V: "",
  videoProviderR2V: "",
  imageBackendDefault: "",
  imageBackendT2I: "",
  imageBackendI2I: "",
  textBackendDefault: "",
  textBackendSimple: "",
  textBackendComplex: "",
  defaultDuration: null,
  videoResolution: null,
  imageResolution: null,
};

/** Read step-two model candidates/global defaults; backends render provider/model names in UI languages. */
async function loadGenerationData(signal: AbortSignal) {
  const [sysConfig, providersRes, customRes, narrationDefaults, auto] = await Promise.all([
    API.getSystemConfig({ signal }),
    API.getProviders({ signal }),
    API.listCustomProviders({ signal }),
    // Prefill is convenience only; unreadable global defaults leave TTS fields empty for users to choose.
    API.getNarrationDefaults({ signal }).catch(() => null),
    // Actual automatically selected models support display/capabilities only; failed reads show Auto only.
    API.getAutoBackends({ signal }).catch(() => null),
  ]);
  const catalogNames = catalogDisplayNames(providersRes.providers, customRes.providers);
  const data: WizardGenerationData = {
    options: {
      video: sysConfig.options.video_backends,
      image: sysConfig.options.image_backends,
      text: sysConfig.options.text_backends,
      audio: sysConfig.options.audio_backends ?? [],
      // Catalog fallback sits below: candidates list ready providers only, while effective saved values may reference providers with lost credentials.
      providerNames: { ...catalogNames.providerNames, ...(sysConfig.options.provider_names ?? {}) },
      modelNames: { ...catalogNames.modelNames, ...(sysConfig.options.model_names ?? {}) },
    },
    providers: providersRes.providers,
    customProviders: customRes.providers,
    globalDefaults: {
      video: sysConfig.settings.default_video_backend ?? "",
      videoI2V: sysConfig.settings.default_video_backend_i2v ?? "",
      videoR2V: sysConfig.settings.default_video_backend_r2v ?? "",
      image: sysConfig.settings.default_image_backend ?? "",
      imageT2I: sysConfig.settings.default_image_backend_t2i ?? "",
      imageI2I: sysConfig.settings.default_image_backend_i2i ?? "",
      textDefault: sysConfig.settings.default_text_backend ?? "",
      textSimple: sysConfig.settings.text_backend_simple ?? "",
      textComplex: sysConfig.settings.text_backend_complex ?? "",
      auto,
    },
  };
  return { data, narrationDefaults };
}

/**
 * New-project wizard: basics, generation settings and style, requiring all three steps before creation.
 * Lobby mounts when `useProjectsStore().showCreateModal` is true, resetting false on close.
 */
export function CreateProjectModal() {
  const { t, i18n } = useTranslation(["dashboard", "common", "templates"]);
  const [, navigate] = useLocation();
  const setShowCreateModal = useProjectsStore((s) => s.setShowCreateModal);
  const hintId = useId();
  const bodyRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState<WizardStep>(1);
  const [basics, setBasics] = useState<WizardBasicsValue>({
    title: "",
    contentMode: null,
    aspectRatio: "9:16",
    generationRoute: null,
    gridStoryboard: false,
  });
  const [duration, setDuration] = useState<WizardDurationValue>({
    targetDuration: 60,
    episodeTargetDuration: null,
    speechRate: null,
  });
  const [models, setModels] = useState<ModelConfigValue>(EMPTY_MODELS);
  // Narration defaults to postproduction dubbing; prefill TTS after global-default reads, showing globals when users switch to TTS.
  const [narration, setNarration] = useState<NarrationDeliveryValue>({
    delivery: "post_production",
    audioBackend: "",
    narrationVoice: "",
    narrationSpeed: null,
  });
  const narrationPrefilled = useRef(false);
  const [style, setStyle] = useState<StylePickerValue>({
    mode: "template",
    templateId: DEFAULT_TEMPLATE_ID,
    activeCategory: "live",
    uploadedFile: null,
    uploadedPreview: null,
  });
  const [creating, setCreating] = useState(false);

  // Read step-two remote data on wizard mounts, never repeat on step switches; refetch on UI-language changes,
  // or catalogs retain prior languages.
  const [generationData, setGenerationData] = useState<WizardGenerationData | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);

  // react-doctor-disable-next-line react-doctor/no-set-state-after-await-in-effect -- postawait writes first check controller.signal.aborted; cleanup abort invalidates previous rounds
  useEffect(() => {
    const controller = new AbortController();
    voidCall((async () => {
      try {
        const { data, narrationDefaults } = await loadGenerationData(controller.signal);
        if (controller.signal.aborted) return;
        if (!narrationPrefilled.current && narrationDefaults) {
          narrationPrefilled.current = true;
          setNarration((prev) => ({
            ...prev,
            audioBackend: narrationDefaults.audio_backend ?? "",
            narrationVoice: narrationDefaults.narration_voice,
            narrationSpeed: narrationDefaults.narration_speed,
          }));
        }
        setGenerationData(data);
        // Successful refetches clear previous errors, preventing them from masking fresh data forever.
        setGenerationError(null);
      } catch (err) {
        if (!controller.signal.aborted) setGenerationError(errMsg(err));
      }
    })());
    return () => controller.abort();
  }, [i18n.language]);

  const close = () => setShowCreateModal(false);

  // After choosing duration/resolution in step two, users can return to step one and change generation modes. Resolution follows execution models only, unchanged without model changes;
  // validate durations against new execution-model full declarations, resetting out-of-set values to Auto. Reference-path narrowing is explained by
  // server-provided causes in step two, guiding reselection; only servers calculate narrowed results, while this handler is synchronous.
  const handleBasicsChange = (next: WizardBasicsValue) => {
    setBasics(next);
    if (next.generationRoute === basics.generationRoute) return;
    const globals = generationData?.globalDefaults ?? { video: "", videoI2V: "", videoR2V: "" };
    const before = executingVideoModel(models, globals, basics.generationRoute === "reference_video");
    const after = executingVideoModel(models, globals, next.generationRoute === "reference_video");
    const nextDurations = catalogDurations(
      generationData?.providers ?? [],
      generationData?.customProviders ?? [],
      after,
    );
    setModels((prev) => ({
      ...prev,
      videoResolution: before !== after ? null : prev.videoResolution,
      defaultDuration:
        prev.defaultDuration !== null && nextDurations?.includes(prev.defaultDuration) ? prev.defaultDuration : null,
    }));
  };

  const isAd = basics.contentMode === "ad";

  // Missing required/correctable fields in current steps, ordered as displayed.
  const missing: string[] = [];
  const invalid: string[] = [];
  if (step === 1) {
    if (!basics.title.trim()) missing.push(t("project_title"));
    if (!basics.contentMode) missing.push(t("content_mode"));
    if (!basics.generationRoute) missing.push(t("generation_route"));
  } else if (step === 2) {
    if (isAd && duration.targetDuration === null) invalid.push(t("target_duration_label"));
    if (!isAd && !isValidEpisodeTargetDuration(duration.episodeTargetDuration)) {
      invalid.push(t("wizard_item_episode_target_duration"));
    }
    if (!isValidSpeechRate(duration.speechRate)) invalid.push(t("wizard_item_speech_rate"));
    const narrationProblem = narrationDeliveryProblem(narration);
    if (narrationProblem === "model") missing.push(t("project_tts_model_label"));
    if (narrationProblem === "voice") missing.push(t("narration_voice_label"));
  }
  const generationLoading = step === 2 && !generationData && !generationError;
  const blocked = missing.length > 0 || invalid.length > 0 || generationLoading;
  const hint =
    missing.length > 0
      ? t("wizard_missing", { items: formatNameList(missing, i18n.language) })
      : invalid.length > 0
        ? t("wizard_invalid", { items: formatNameList(invalid, i18n.language) })
        : null;

  const goTo = (next: WizardStep) => {
    setStep(next);
    // Three steps share one scroll area; new steps start at top.
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  };

  const handleCreate = async () => {
    const { contentMode, generationRoute } = basics;
    if (!contentMode || !generationRoute) return;
    setCreating(true);
    try {
      // Resolution model_settings keys use execution models: backends query by execution identities. Wizards expose defaults only,
      // but global subtiers may select different execution models; mismatched keys silently lose resolution settings.
      const globals = generationData?.globalDefaults ?? {
        video: "",
        videoI2V: "",
        videoR2V: "",
        image: "",
        imageT2I: "",
      };
      const executingVideo = executingVideoModel(models, globals, generationRoute === "reference_video");
      const executingImage = executingImageModel(models, globals);
      const modelSettings: Record<string, { resolution: string }> = {};
      if (executingVideo && models.videoResolution) {
        modelSettings[executingVideo] = { resolution: models.videoResolution };
      }
      if (executingImage && models.imageResolution) {
        modelSettings[executingImage] = { resolution: models.imageResolution };
      }

      const resp = await API.createProject({
        title: basics.title.trim(),
        content_mode: contentMode,
        aspect_ratio: basics.aspectRatio,
        generation_mode: generationRoute,
        grid_storyboard: basics.gridStoryboard,
        // Omit unset estimated speaking rates; servers do not persist them and use language defaults.
        ...(duration.speechRate !== null ? { speech_rate_units_per_second: duration.speechRate } : {}),
        // Ads omit default_duration, planning shots from total targets; send target_duration instead.
        ...(contentMode === "ad"
          ? { target_duration: duration.targetDuration ?? undefined }
          : {
              default_duration: models.defaultDuration,
              // Omit unset targets; servers do not persist or inject soft planning constraints.
              ...(duration.episodeTargetDuration !== null
                ? { episode_target_duration: duration.episodeTargetDuration }
                : {}),
            }),
        style_template_id: style.mode === "template" ? style.templateId : null,
        video_backend: models.videoBackend || null,
        default_image_backend: models.imageBackendDefault || null,
        default_text_backend: models.textBackendDefault || null,
        text_backend_simple: models.textBackendSimple || null,
        text_backend_complex: models.textBackendComplex || null,
        ...(Object.keys(modelSettings).length > 0 ? { model_settings: modelSettings } : {}),
        narration_delivery: narration.delivery,
        // Explicitly submit all three TTS snapshot fields, persisting displayed wizard values without server global-default supplementation.
        ...(narration.delivery === "use_tts"
          ? {
              audio_backend: narration.audioBackend,
              narration_voice: narration.narrationVoice.trim(),
              narration_speed: narration.narrationSpeed,
            }
          : {}),
      });

      if (style.mode === "custom" && style.uploadedFile) {
        try {
          await API.uploadStyleImage(resp.name, style.uploadedFile);
        } catch {
          useAppStore.getState().pushToast(t("style_upload_failed_hint"), "warning");
        }
      }

      setShowCreateModal(false);
      navigate(`/app/projects/${resp.name}`);
    } catch (err) {
      useAppStore.getState().pushToast(t("create_project_failed", { message: errMsg(err) }), "error");
      setCreating(false);
    }
  };

  const handleNext = () => {
    if (blocked) return;
    if (step === 3) {
      voidPromise(handleCreate)();
      return;
    }
    goTo((step + 1) as WizardStep);
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        // In-flight creation ignores closing by Esc, close buttons or backdrop clicks.
        if (!open && !creating) close();
      }}
    >
      <DialogContent size="wizard" initialFocus={titleRef}>
        <DialogHeader>
          <DialogTitle>{t("new_project")}</DialogTitle>
          <div className="mt-1.5">
            <WizardStepper current={step} />
          </div>
        </DialogHeader>

        <DialogBody ref={bodyRef}>
          {step === 1 && <WizardStepBasics value={basics} onChange={handleBasicsChange} titleRef={titleRef} />}
          {step === 2 && (
            <WizardStepGeneration
              isAd={isAd}
              usesReferenceImages={basics.generationRoute === "reference_video"}
              duration={duration}
              onDurationChange={setDuration}
              models={models}
              onModelsChange={setModels}
              narration={narration}
              onNarrationChange={setNarration}
              data={generationData}
              error={generationError}
            />
          )}
          {step === 3 && <StylePicker value={style} onChange={setStyle} />}
        </DialogBody>

        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={creating}>
            {t("common:cancel")}
          </Button>
          <p id={hintId} className="min-w-0 flex-1 text-right text-xs text-muted-foreground">
            {hint}
          </p>
          {step > 1 && (
            <Button variant="outline" onClick={() => goTo((step - 1) as WizardStep)} disabled={creating}>
              {t("templates:prev_step")}
            </Button>
          )}
          <Button onClick={handleNext} disabled={blocked || creating} aria-describedby={hint ? hintId : undefined}>
            {creating && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {step === 3 ? t("create_project") : t("templates:next_step")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
