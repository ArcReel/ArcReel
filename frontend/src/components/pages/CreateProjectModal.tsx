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
import { isValidEpisodeTargetDuration } from "@/components/shared/EpisodeTargetDurationField";
import { isValidSpeechRate } from "@/components/shared/SpeechRateField";
import { executingImageModel, executingVideoModel } from "@/components/shared/LayeredModelFields";
import type { ModelConfigValue } from "@/components/shared/ModelConfigSection";
import { narrationDeliveryProblem, type NarrationDeliveryValue } from "@/components/shared/NarrationDeliveryFields";
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

/** 读取第二步的可选模型与全局默认；供应商与模型名由后端按界面语言成文。 */
async function loadGenerationData(signal: AbortSignal) {
  const [sysConfig, providersRes, customRes, narrationDefaults] = await Promise.all([
    API.getSystemConfig({ signal }),
    API.getProviders({ signal }),
    API.listCustomProviders({ signal }),
    // 预填只是便利：读不到全局默认时 TTS 字段留空，由用户自选
    API.getNarrationDefaults({ signal }).catch(() => null),
  ]);
  const catalogNames = catalogDisplayNames(providersRes.providers, customRes.providers);
  const data: WizardGenerationData = {
    options: {
      video: sysConfig.options.video_backends,
      image: sysConfig.options.image_backends,
      text: sysConfig.options.text_backends,
      audio: sysConfig.options.audio_backends ?? [],
      // 目录兜底层在下：候选只列 ready 供应商，而已配置的生效值可能指向失去凭证的那个。
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
    },
  };
  return { data, narrationDefaults };
}

/**
 * 新建项目向导：基础信息、生成设置、风格三步，走完三步才能创建。
 * 由项目大厅在 `useProjectsStore().showCreateModal` 为 true 时挂载，关闭时把它置回 false。
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
  // 旁白交付缺省后期配音；TTS 字段在全局默认取回后预填，用户切到 TTS 时看到的即全局默认
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

  // 第二步的远端数据在向导挂载时读取，切换步骤不重复请求；界面语言变化后重取，
  // 否则目录停留在切换前的语言。
  const [generationData, setGenerationData] = useState<WizardGenerationData | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);

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
        // 重取成功即清掉上一轮的错误，否则错误会一直遮住新数据。
        setGenerationError(null);
      } catch (err) {
        if (!controller.signal.aborted) setGenerationError(errMsg(err));
      }
    })());
    return () => controller.abort();
  }, [i18n.language]);

  // blob: URL 所有权集中在此：StylePicker 只通过 onChange 更换引用，
  // revoke 统一由本 effect 在 URL 变更或卸载时触发。非 blob: 跳过。
  useEffect(() => {
    const url = style.uploadedPreview;
    if (!url?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(url);
  }, [style.uploadedPreview]);

  const close = () => setShowCreateModal(false);

  // 第二步选好时长与分辨率后还能退回第一步改生成模式。分辨率只由执行模型决定，模型没换就不动；
  // 时长按新执行模型的声明全集校验，落在全集外的退回自动。参考图路径把该值收窄掉的情形由
  // 第二步按服务端成因的提示引导重选——收窄结果只有服务端能给，这里是同步事件处理器。
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

  // 当前步骤还缺的必填项与需要修正的字段，按界面上的顺序列出
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
    // 三步共用一个滚动区，进入新步骤时从顶部开始
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  };

  const handleCreate = async () => {
    const { contentMode, generationRoute } = basics;
    if (!contentMode || !generationRoute) return;
    setCreating(true);
    try {
      // resolution 的 model_settings key 用执行模型：后端按执行模型查这张表，向导只暴露默认层，
      // 但全局细分层若指向别的模型，执行的就不是默认层那个——键位对不上分辨率会被静默忽略。
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
        // 口播语速估算未填即不传（服务端不落盘，回退语言默认）
        ...(duration.speechRate !== null ? { speech_rate_units_per_second: duration.speechRate } : {}),
        // 广告项目不暴露 default_duration（按目标总时长逐个分镜规划），改传 target_duration
        ...(contentMode === "ad"
          ? { target_duration: duration.targetDuration ?? undefined }
          : {
              default_duration: models.defaultDuration,
              // 未设目标即不传（服务端不落盘，脚本规划不注入该软约束）
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
        // TTS 快照三项显式提交：落盘的就是向导里看到的值，服务端不再按全局默认补
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
        // 创建请求在途时忽略关闭请求（Esc、关闭按钮、点击遮罩）
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
