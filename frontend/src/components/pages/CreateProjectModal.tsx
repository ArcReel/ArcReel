
import { useState, useEffect, useRef, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { errMsg, voidCall, voidPromise } from "@/utils/async";
import { useLocation } from "wouter";
import { Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { DEFAULT_TEMPLATE_ID } from "@/data/style-templates";
import { PROVIDER_NAMES } from "@/components/ui/ProviderIcon";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { WizardStep1Basics, type WizardStep1Value } from "./create-project/WizardStep1Basics";
import { WizardStep2Models, type WizardStep2Data } from "./create-project/WizardStep2Models";
import { WizardStep3Style, type WizardStep3Value } from "./create-project/WizardStep3Style";
import type { ModelConfigValue } from "@/components/shared/ModelConfigSection";

// 新建项目对话框 · "Open Reel"
// 仪式感来自项目大厅的 Darkroom 美学：editorial 衬线 + mono 标尺线 + sprocket 胶片孔。
// 三步走作为录制流程的开机预备：身份 → 器材 → 镜头美学。

const SPROCKET_STYLE: CSSProperties = {
  background: "repeating-linear-gradient(90deg, oklch(0 0 0 / 0.55) 0 6px, transparent 6px 12px)",
};

// ─── Step indicator ───────────────────────────────────────────────────────────

const STEPS = [
  { num: 1, key: "wizard_step_basics" },
  { num: 2, key: "wizard_step_models" },
  { num: 3, key: "wizard_step_style" },
] as const;

function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  const { t } = useTranslation("templates");
  return (
    <div className="relative">
      {/* sprocket 上下边 — 暗示一段胶片正在过卷头 */}
      <div aria-hidden className="absolute inset-x-6 top-0 h-[3px] opacity-40" style={SPROCKET_STYLE} />
      <div aria-hidden className="absolute inset-x-6 bottom-0 h-[3px] opacity-40" style={SPROCKET_STYLE} />

      <ol className="relative flex items-stretch py-5">
        {STEPS.map((s, i) => {
          const done = current > s.num;
          const active = current === s.num;
          const last = i === STEPS.length - 1;
          return (
            <li
              key={s.num}
              className={"relative flex flex-1 items-center" + (last ? "" : " pr-3")}
              aria-current={active ? "step" : undefined}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <span
                  className={
                    "grid h-7 w-7 shrink-0 place-items-center rounded-[8px] font-mono text-[11px] font-bold tabular-nums transition-colors " +
                    (done
                      ? "border border-accent/45 text-text"
                      : active
                        ? "border border-accent/55 text-text"
                        : "border border-hairline-soft text-text-4")
                  }
                  style={
                    done || active
                      ? {
                          background:
                            "linear-gradient(180deg, oklch(0.30 0.05 295 / 0.65), oklch(0.20 0.02 280 / 0.65))",
                          boxShadow: active
                            ? "inset 0 1px 0 oklch(1 0 0 / 0.06), 0 0 18px -6px var(--color-accent-glow)"
                            : "inset 0 1px 0 oklch(1 0 0 / 0.05)",
                        }
                      : { background: "oklch(0.16 0.010 265 / 0.55)" }
                  }
                >
                  {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : s.num.toString().padStart(2, "0")}
                </span>
                <div className="min-w-0">
                  <div
                    className={
                      "font-mono text-[9.5px] font-bold uppercase tracking-[0.14em] " +
                      (active ? "text-accent-2" : done ? "text-text-3" : "text-text-4")
                    }
                  >
                    Step {s.num.toString().padStart(2, "0")}
                  </div>
                  <div
                    className={
                      "text-[12.5px] tracking-tight truncate " +
                      (active ? "text-text font-semibold" : done ? "text-text-2" : "text-text-3")
                    }
                  >
                    {t(s.key)}
                  </div>
                </div>
              </div>
              {!last && (
                <div
                  aria-hidden
                  className="ml-3 flex-1"
                  style={{
                    height: 1,
                    background:
                      done
                        ? "linear-gradient(90deg, var(--color-accent), oklch(0.55 0.06 295 / 0.4))"
                        : "var(--color-hairline-soft)",
                  }}
                />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CreateProjectModal() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, navigate] = useLocation();
  const { setShowCreateModal } = useProjectsStore();

  const [step, setStep] = useState<1 | 2 | 3>(1);

  const [basics, setBasics] = useState<WizardStep1Value>({
    title: "",
    contentMode: "narration",
    aspectRatio: "9:16",
    generationMode: "storyboard",
  });

  const [models, setModels] = useState<ModelConfigValue>({
    videoBackend: "",
    imageBackendT2I: "",
    imageBackendI2I: "",
    textBackendScript: "",
    textBackendOverview: "",
    textBackendStyle: "",
    defaultDuration: null,
    videoResolution: null,
    imageResolution: null,
  });

  const [style, setStyle] = useState<WizardStep3Value>({
    mode: "template",
    templateId: DEFAULT_TEMPLATE_ID,
    activeCategory: "live",
    uploadedFile: null,
    uploadedPreview: null,
  });

  const [creating, setCreating] = useState(false);

  // Step2 的远端数据 hoist 到此处：只在 modal 挂载时 fetch 一次，
  // 前进/后退切 step 时 Step2 unmount/mount 不再触发 HTTP。
  const [step2Data, setStep2Data] = useState<WizardStep2Data | null>(null);
  const [step2Error, setStep2Error] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    voidCall((async () => {
      try {
        const [sysConfig, providersRes, customRes] = await Promise.all([
          API.getSystemConfig(),
          API.getProviders(),
          API.listCustomProviders(),
        ]);
        if (cancelled) return;
        setStep2Data({
          options: {
            video: sysConfig.options.video_backends,
            image: sysConfig.options.image_backends,
            text: sysConfig.options.text_backends,
            providerNames: { ...PROVIDER_NAMES, ...(sysConfig.options.provider_names ?? {}) },
          },
          providers: providersRes.providers,
          customProviders: customRes.providers,
          globalDefaults: {
            video: sysConfig.settings.default_video_backend ?? "",
            imageT2I:
              sysConfig.settings.default_image_backend_t2i ??
              sysConfig.settings.default_image_backend ??
              "",
            imageI2I:
              sysConfig.settings.default_image_backend_i2i ??
              sysConfig.settings.default_image_backend ??
              "",
            textScript: sysConfig.settings.text_backend_script ?? "",
            textOverview: sysConfig.settings.text_backend_overview ?? "",
            textStyle: sysConfig.settings.text_backend_style ?? "",
          },
        });
      } catch (err) {
        if (!cancelled) setStep2Error(errMsg(err));
      }
    })());
    return () => {
      cancelled = true;
    };
  }, []);

  // blob: URL 所有权集中在此：StylePicker 只通过 onChange 更换引用，
  // revoke 统一由本 effect 在 URL 变更或 unmount 时触发。非 blob: 跳过。
  useEffect(() => {
    const url = style.uploadedPreview;
    if (!url?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(url);
  }, [style.uploadedPreview]);

  const handleClose = () => {
    setShowCreateModal(false);
  };

  useEscapeClose(() => setShowCreateModal(false));

  // 背景 inert：打开期间屏蔽 #root 内容（modal 通过 portal 挂到 body，
  // 不在 #root 子树内，因此不会被 inert 传染）。
  useEffect(() => {
    const root = document.getElementById("root");
    if (!root) return;
    root.setAttribute("aria-hidden", "true");
    root.setAttribute("inert", "");
    return () => {
      root.removeAttribute("aria-hidden");
      root.removeAttribute("inert");
    };
  }, []);

  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true);

  const handleCreate = async () => {
    setCreating(true);
    try {
      // resolution 的 model_settings key 用 effective backend（项目覆盖 ‖ 全局默认），
      // 否则用户在"跟随全局默认"路径下选的分辨率会丢失。
      const effectiveVideo = models.videoBackend || step2Data?.globalDefaults.video || "";
      const effectiveImageT2I = models.imageBackendT2I || step2Data?.globalDefaults.imageT2I || "";
      const modelSettings: Record<string, { resolution: string }> = {};
      if (effectiveVideo && models.videoResolution) {
        modelSettings[effectiveVideo] = { resolution: models.videoResolution };
      }
      if (effectiveImageT2I && models.imageResolution) {
        modelSettings[effectiveImageT2I] = { resolution: models.imageResolution };
      }

      const resp = await API.createProject({
        title: basics.title.trim(),
        content_mode: basics.contentMode,
        aspect_ratio: basics.aspectRatio,
        generation_mode: basics.generationMode,
        default_duration: models.defaultDuration,
        style_template_id: style.mode === "template" ? style.templateId : null,
        video_backend: models.videoBackend || null,
        image_provider_t2i: models.imageBackendT2I || null,
        image_provider_i2i: models.imageBackendI2I || null,
        text_backend_script: models.textBackendScript || null,
        text_backend_overview: models.textBackendOverview || null,
        text_backend_style: models.textBackendStyle || null,
        ...(Object.keys(modelSettings).length > 0 ? { model_settings: modelSettings } : {}),
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

      setShowCreateModal(false);
      navigate(`/app/projects/${resp.name}`);
    } catch (err) {
      useAppStore.getState().pushToast(
        `${t("dashboard:create_project_failed")}${errMsg(err)}`,
        "error"
      );
    } finally {
      setCreating(false);
    }
  };

  const stepKicker = `Reel ${step.toString().padStart(2, "0")} / 03`;

  const modal = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{
        background:
          "radial-gradient(900px 480px at 12% -10%, oklch(0.32 0.05 295 / 0.30), transparent 55%), radial-gradient(800px 460px at 100% 110%, oklch(0.26 0.04 260 / 0.28), transparent 55%), oklch(0 0 0 / 0.62)",
        backdropFilter: "blur(12px) saturate(1.1)",
        WebkitBackdropFilter: "blur(12px) saturate(1.1)",
      }}
    >
      {/* 遮罩层：点击关闭。键盘路径走 Esc。 */}
      <button
        type="button"
        aria-label={t("common:close")}
        tabIndex={-1}
        onClick={handleClose}
        className="absolute inset-0 cursor-default bg-transparent"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        className="relative w-full max-w-3xl overflow-hidden rounded-[14px] border border-hairline bg-bg-grad-a/95 shadow-[0_40px_100px_-30px_oklch(0_0_0_/_0.85)] backdrop-blur-md max-h-[92vh] flex flex-col"
        style={{
          background:
            "linear-gradient(180deg, oklch(0.20 0.012 270 / 0.95), oklch(0.16 0.010 265 / 0.95))",
        }}
      >
        {/* Hero header */}
        <div className="relative shrink-0 px-7 pt-6 pb-5">
          {/* 角落装饰 — 取景框的轮廓 */}
          <div
            aria-hidden
            className="pointer-events-none absolute left-3 top-3 h-3 w-3 border-l border-t border-accent/40"
          />
          <div
            aria-hidden
            className="pointer-events-none absolute right-3 top-3 h-3 w-3 border-r border-t border-accent/40"
          />

          <button
            type="button"
            onClick={handleClose}
            aria-label={t("common:close")}
            className="absolute right-5 top-5 grid h-8 w-8 place-items-center rounded-md border border-hairline-soft bg-bg/55 text-text-3 transition-colors hover:border-hairline hover:bg-bg hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <X className="h-4 w-4" />
          </button>

          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
            {stepKicker}
          </div>
          <h2
            id="create-project-title"
            className="font-editorial mt-1.5"
            style={{
              fontWeight: 400,
              fontSize: 36,
              lineHeight: 1.05,
              letterSpacing: "-0.012em",
              color: "var(--color-text)",
            }}
          >
            {t("dashboard:new_project")}
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-[1.55] text-text-3">
            {t("templates:wizard_step_basics")}
            <span aria-hidden className="mx-1.5 text-text-4">/</span>
            {t("templates:wizard_step_models")}
            <span aria-hidden className="mx-1.5 text-text-4">/</span>
            {t("templates:wizard_step_style")}
          </p>
        </div>

        {/* Step indicator strip */}
        <div className="shrink-0 border-y border-hairline-soft bg-[oklch(0.16_0.010_265_/_0.55)] px-6">
          <StepIndicator current={step} />
        </div>

        {/* Current step body */}
        <div className="min-h-0 flex-1 overflow-y-auto px-7 pt-6 pb-7">
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
              data={step2Data}
              error={step2Error}
            />
          )}
          {step === 3 && (
            <WizardStep3Style
              value={style}
              onChange={setStyle}
              onBack={() => setStep(2)}
              onCreate={voidPromise(handleCreate)}
              onCancel={handleClose}
              creating={creating}
            />
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
