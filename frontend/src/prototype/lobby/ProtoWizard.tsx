// PROTOTYPE — 新建项目向导（#2979）。「两种向导外框 + 现状，?wizard= 切换，&wh= 切换固定高 / 随内容。」
//
// 字段按「项目大厅、新建项目向导」结论重新分配：
//   1 基础信息：标题、内容模式（剧情演绎在前、无默认值）、画幅、生成模式、宫格分镜（仅分镜模式）
//   2 生成设置：时长（广告选目标时长，其余选单集目标时长和语速估算）、模型、旁白配音
//   3 风格：风格模板或自定义图片
// 两个外框都是 Header / Body / Footer 三段、Body 唯一滚动（「滚动失效的系统性排查」的 Dialog 规则）。
//   B 顶部步骤条：一条横向步骤指示器在头部
//   C 左侧步骤栏：竖向步骤，已完成的步骤下方显示所选摘要，点击可回到该步
// 字段控件直接复用现有共享组件（视觉迁移不在本票），「创建项目」是桩。

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { Button } from "@/components/ui/button";
import { Dialog, DialogOverlay, DialogPortal, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { GenerationRouteCards } from "@/components/shared/GenerationRouteCards";
import { GridStoryboardBar } from "@/components/shared/GridStoryboardBar";
import { EpisodeTargetDurationField, isValidEpisodeTargetDuration } from "@/components/shared/EpisodeTargetDurationField";
import { SpeechRateField, isValidSpeechRate } from "@/components/shared/SpeechRateField";
import { ModelConfigSection, type ModelConfigValue } from "@/components/shared/ModelConfigSection";
import {
  NarrationDeliveryFields,
  narrationDeliveryProblem,
  type NarrationDeliveryValue,
} from "@/components/shared/NarrationDeliveryFields";
import { StylePicker } from "@/components/shared/StylePicker";
import type { WizardStep2Data } from "@/components/pages/create-project/WizardStep2Models";
import type { WizardStep3Value } from "@/components/pages/create-project/WizardStep3Style";
import { DEFAULT_TEMPLATE_ID } from "@/data/style-templates";
import { catalogDisplayNames } from "@/utils/provider-models";
import type { GenerationRoute } from "@/utils/generation-mode";
import type { WizardVariant } from "./proto-params";

type ContentMode = "drama" | "narration" | "ad";
type Step = 1 | 2 | 3;

const STEPS: { n: Step; label: string; hint: string }[] = [
  { n: 1, label: "基础信息", hint: "项目叫什么、讲什么、怎么生成。生成模式创建后不能更改。" },
  { n: 2, label: "生成设置", hint: "时长、模型与旁白配音。都可以在项目设置里再改。" },
  { n: 3, label: "风格", hint: "选一个风格模板，或上传一张参考图。也可以先不选。" },
];

const CONTENT_MODES: { key: ContentMode; label: string; desc: string }[] = [
  { key: "drama", label: "剧情演绎", desc: "角色对话与剧情推动画面，适合小说改编等故事型内容。" },
  { key: "narration", label: "说书/旁白", desc: "一条旁白贯穿整集，画面配合旁白推进，适合解说与知识类。" },
  { key: "ad", label: "广告/短片", desc: "单条成片，按目标总时长规划分镜，适合带货与创意短片。" },
];

const ROUTE_LABEL: Record<GenerationRoute, string> = { storyboard: "分镜图生视频", reference_video: "参考生视频" };
const AD_TIERS = [15, 30, 60, 90] as const;

interface Basics {
  title: string;
  contentMode: ContentMode | null;
  aspectRatio: "9:16" | "16:9";
  route: GenerationRoute | null;
  grid: boolean;
}
interface Timing {
  adTarget: number;
  episodeTarget: number | null;
  speechRate: number | null;
}

// ---------------------------------------------------------------------------
// 字段（两个外框共用，外框才是被比较的对象）
// ---------------------------------------------------------------------------

function Section({ title, desc, children }: { title: string; desc?: string; children: ReactNode }) {
  return (
    <section className="space-y-2.5">
      <div>
        <h3 className="text-[13px] font-medium text-foreground">{title}</h3>
        {desc && <p className="mt-0.5 text-[12px] text-muted-foreground">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

function choiceCls(active: boolean) {
  return cn(
    "flex cursor-pointer flex-col gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
    active ? "border-primary/60 bg-primary/10" : "border-border [@media(hover:hover)]:hover:border-input [@media(hover:hover)]:hover:bg-accent",
  );
}

function Step1({ v, set, titleError }: { v: Basics; set: (n: Basics) => void; titleError: boolean }) {
  return (
    <div className="space-y-6">
      <Section title="项目标题">
        <Input
          value={v.title}
          onChange={(e) => set({ ...v, title: e.target.value })}
          placeholder="例如：重生之皇后威武"
          aria-label="项目标题"
          aria-invalid={titleError || undefined}
          className="h-9 text-[14px]"
        />
        <p className="text-[12px] text-muted-foreground">系统会另外生成项目 ID，用于网址与文件存储；以后改标题不影响 ID。</p>
      </Section>

      <Section title="内容模式" desc="决定脚本的写法。必选，没有默认值。">
        <div role="radiogroup" aria-label="内容模式" className="grid grid-cols-3 gap-2.5">
          {CONTENT_MODES.map((m) => (
            <label key={m.key} className={choiceCls(v.contentMode === m.key)}>
              <input
                type="radio"
                name="proto-content-mode"
                className="sr-only"
                checked={v.contentMode === m.key}
                onChange={() => set({ ...v, contentMode: m.key, grid: m.key === "ad" ? false : v.grid })}
              />
              <span className="text-[13px] font-medium">{m.label}</span>
              <span className="text-[12px] leading-[1.45] text-muted-foreground">{m.desc}</span>
            </label>
          ))}
        </div>
      </Section>

      <Section title="画幅">
        <div role="radiogroup" aria-label="画幅" className="flex gap-2.5">
          {(["9:16", "16:9"] as const).map((r) => (
            <label key={r} className={cn(choiceCls(v.aspectRatio === r), "flex-row items-center gap-2 py-2")}>
              <input type="radio" name="proto-aspect" className="sr-only" checked={v.aspectRatio === r} onChange={() => set({ ...v, aspectRatio: r })} />
              <span aria-hidden className={cn("rounded-[2px] border border-text-3", r === "9:16" ? "h-3.5 w-2" : "h-2 w-3.5")} />
              <span className="text-[13px]">{r === "9:16" ? "竖屏 9:16" : "横屏 16:9"}</span>
            </label>
          ))}
        </div>
      </Section>

      <GenerationRouteCards value={v.route} onChange={(route) => set({ ...v, route, grid: route === "storyboard" ? v.grid : false })}>
        {v.route === "storyboard" && v.contentMode !== "ad" ? (
          <GridStoryboardBar checked={v.grid} onToggle={(grid) => set({ ...v, grid })} animated />
        ) : null}
      </GenerationRouteCards>
    </div>
  );
}

function Step2({ basics, timing, setTiming, models, setModels, narration, setNarration, data, error }: {
  basics: Basics;
  timing: Timing;
  setTiming: (t: Timing) => void;
  models: ModelConfigValue;
  setModels: (m: ModelConfigValue) => void;
  narration: NarrationDeliveryValue;
  setNarration: (n: NarrationDeliveryValue) => void;
  data: WizardStep2Data | null;
  error: string | null;
}) {
  const isAd = basics.contentMode === "ad";
  return (
    <div className="space-y-6">
      <Section title="时长" desc={isAd ? "整条成片的目标长度，分镜按它规划。" : "都可以不填：不设目标时按脚本自然长度，语速按项目语言的默认值估算。"}>
        {isAd ? (
          <div role="radiogroup" aria-label="目标总时长" className="flex gap-2">
            {AD_TIERS.map((s) => (
              <label key={s} className={cn(choiceCls(timing.adTarget === s), "px-3.5 py-1.5")}>
                <input type="radio" name="proto-ad" className="sr-only" checked={timing.adTarget === s} onChange={() => setTiming({ ...timing, adTarget: s })} />
                <span className="text-[13px] tabular-nums">{s} 秒</span>
              </label>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <EpisodeTargetDurationField value={timing.episodeTarget} onChange={(episodeTarget) => setTiming({ ...timing, episodeTarget })} />
            <SpeechRateField value={timing.speechRate} onChange={(speechRate) => setTiming({ ...timing, speechRate })} />
          </div>
        )}
      </Section>

      {!data && !error && <p className="py-8 text-center text-muted-foreground">正在读取可用模型…</p>}
      {error && <p className="rounded-lg border border-border px-4 py-6 text-center text-destructive">{error}</p>}
      {data && (
        <ModelConfigSection
          showSubFields={false}
          value={models}
          onChange={setModels}
          providers={data.providers}
          customProviders={data.customProviders}
          options={{
            videoBackends: data.options.video,
            imageBackends: data.options.image,
            textBackends: data.options.text,
            providerNames: data.options.providerNames,
            modelNames: data.options.modelNames,
          }}
          globalDefaults={data.globalDefaults}
          usesReferenceImages={basics.route === "reference_video"}
          enable={isAd ? { duration: false } : undefined}
        />
      )}
      {data && (
        <Section title="旁白配音">
          <NarrationDeliveryFields
            value={narration}
            onChange={setNarration}
            audioBackends={data.options.audio}
            providerNames={data.options.providerNames}
            modelNames={data.options.modelNames}
          />
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 外框
// ---------------------------------------------------------------------------

export function ProtoWizard({ variant, height, onClose }: { variant: Exclude<WizardVariant, "A">; height: "fixed" | "auto"; onClose: () => void }) {
  const { i18n } = useTranslation();
  const [step, setStep] = useState<Step>(1);
  const [reached, setReached] = useState<Step>(1);
  const [titleError, setTitleError] = useState(false);
  const [basics, setBasics] = useState<Basics>({ title: "", contentMode: null, aspectRatio: "9:16", route: null, grid: false });
  const [timing, setTiming] = useState<Timing>({ adTarget: 60, episodeTarget: null, speechRate: null });
  const [models, setModels] = useState<ModelConfigValue>({
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
  });
  const [narration, setNarration] = useState<NarrationDeliveryValue>({
    delivery: "post_production",
    audioBackend: "",
    narrationVoice: "",
    narrationSpeed: null,
  });
  const [style, setStyle] = useState<WizardStep3Value>({
    mode: "template",
    templateId: DEFAULT_TEMPLATE_ID,
    activeCategory: "live",
    uploadedFile: null,
    uploadedPreview: null,
  });
  const [data, setData] = useState<WizardStep2Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // 与现状向导相同的取数（只读）
  useEffect(() => {
    let cancelled = false;
    Promise.all([API.getSystemConfig(), API.getProviders(), API.listCustomProviders()])
      .then(([sys, prov, custom]) => {
        if (cancelled) return;
        const names = catalogDisplayNames(prov.providers, custom.providers);
        setData({
          options: {
            video: sys.options.video_backends,
            image: sys.options.image_backends,
            text: sys.options.text_backends,
            audio: sys.options.audio_backends ?? [],
            providerNames: { ...names.providerNames, ...(sys.options.provider_names ?? {}) },
            modelNames: { ...names.modelNames, ...(sys.options.model_names ?? {}) },
          },
          providers: prov.providers,
          customProviders: custom.providers,
          globalDefaults: {
            video: sys.settings.default_video_backend ?? "",
            videoI2V: sys.settings.default_video_backend_i2v ?? "",
            videoR2V: sys.settings.default_video_backend_r2v ?? "",
            image: sys.settings.default_image_backend ?? "",
            imageT2I: sys.settings.default_image_backend_t2i ?? "",
            imageI2I: sys.settings.default_image_backend_i2i ?? "",
            textDefault: sys.settings.default_text_backend ?? "",
            textSimple: sys.settings.text_backend_simple ?? "",
            textComplex: sys.settings.text_backend_complex ?? "",
          },
        });
      })
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [i18n.language]);

  const step1Ok = !!basics.title.trim() && !!basics.contentMode && !!basics.route;
  const step2Ok =
    !!data &&
    narrationDeliveryProblem(narration) === null &&
    isValidSpeechRate(timing.speechRate) &&
    isValidEpisodeTargetDuration(timing.episodeTarget);
  const canNext = step === 1 ? step1Ok : step === 2 ? step2Ok : true;

  const go = (s: Step) => {
    setStep(s);
    setReached((r) => (s > r ? s : r));
    bodyRef.current?.scrollTo({ top: 0 });
  };
  const next = () => {
    if (step === 1 && !basics.title.trim()) setTitleError(true);
    if (!canNext) return;
    if (step === 3) {
      useAppStore.getState().pushToast("原型：「创建项目」不会写入数据", "info");
      onClose();
      return;
    }
    go((step + 1) as Step);
  };

  const missing =
    step === 1 && !step1Ok
      ? `还需要：${[!basics.title.trim() && "标题", !basics.contentMode && "内容模式", !basics.route && "生成模式"].filter(Boolean).join("、")}`
      : null;

  const summaries: Record<Step, string | null> = {
    1: step1Ok
      ? [CONTENT_MODES.find((m) => m.key === basics.contentMode)?.label, basics.aspectRatio, basics.route && ROUTE_LABEL[basics.route], basics.grid && "宫格分镜"]
          .filter(Boolean)
          .join(" · ")
      : null,
    2:
      reached > 2
        ? [
            basics.contentMode === "ad" ? `目标 ${timing.adTarget} 秒` : timing.episodeTarget ? `单集约 ${timing.episodeTarget} 秒` : "不设时长目标",
            narration.delivery === "use_tts" ? "TTS 配音" : "后期配音",
          ].join(" · ")
        : null,
    3: null,
  };

  const body = (
    <div ref={bodyRef} data-scroll-owner className="relative min-h-0 flex-1 overflow-y-auto px-6 py-5 [scrollbar-gutter:stable]">
      {step === 1 && (
        <Step1
          v={basics}
          set={(n) => {
            setBasics(n);
            if (n.title.trim()) setTitleError(false);
          }}
          titleError={titleError}
        />
      )}
      {step === 2 && (
        <Step2
          basics={basics}
          timing={timing}
          setTiming={setTiming}
          models={models}
          setModels={setModels}
          narration={narration}
          setNarration={setNarration}
          data={data}
          error={error}
        />
      )}
      {step === 3 && <StylePicker value={style} onChange={setStyle} />}
    </div>
  );

  const footer = (
    <div className="flex shrink-0 items-center gap-2 border-t border-border bg-muted/30 px-6 py-3">
      <Button variant="ghost" onClick={onClose}>
        取消
      </Button>
      {missing && <span className="ml-auto text-[12px] text-muted-foreground">{missing}</span>}
      <div className={cn("flex gap-2", !missing && "ml-auto")}>
        {step > 1 && (
          <Button variant="outline" onClick={() => go((step - 1) as Step)}>
            上一步
          </Button>
        )}
        <Button onClick={next} disabled={!canNext}>
          {step === 3 ? "创建项目" : "下一步"}
        </Button>
      </div>
    </div>
  );

  const closeBtn = (
    <DialogPrimitive.Close render={<Button variant="ghost" size="icon-sm" aria-label="关闭" className="absolute top-3 right-3" />}>
      <X />
    </DialogPrimitive.Close>
  );

  // 固定高：三步同高，切换步骤时外框不跳；随内容：高度跟着当前步的内容走，上限为视口
  const heightCls = height === "fixed" ? "h-[min(760px,calc(100dvh-48px))]" : "max-h-[calc(100dvh-48px)]";

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogPortal>
        <DialogOverlay className="bg-black/60 supports-backdrop-filter:backdrop-blur-none" />
        <DialogPrimitive.Popup
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl bg-popover text-sm text-popover-foreground ring-1 ring-foreground/10 outline-none",
            "duration-150 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
            variant === "B" ? "max-w-[720px] flex-col" : "max-w-[900px] flex-row",
            heightCls,
          )}
        >
          {variant === "B" ? (
            <>
              <div className="relative shrink-0 border-b border-border px-6 pt-5 pb-4">
                <DialogTitle className="text-[17px]">新建项目</DialogTitle>
                <ol className="mt-3.5 flex items-center gap-2" aria-label="步骤">
                  {STEPS.map((s, i) => {
                    const done = s.n < step;
                    const active = s.n === step;
                    return (
                      <li key={s.n} className="flex items-center gap-2" aria-current={active ? "step" : undefined}>
                        <span
                          className={cn(
                            "grid size-5 place-items-center rounded-full text-[11px] tabular-nums",
                            active ? "bg-primary text-primary-foreground" : done ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
                          )}
                        >
                          {done ? <Check className="size-3" /> : s.n}
                        </span>
                        <span className={cn("text-[13px]", active ? "font-medium text-foreground" : "text-muted-foreground")}>{s.label}</span>
                        {i < STEPS.length - 1 && <span aria-hidden className="mx-1 h-px w-8 bg-border" />}
                      </li>
                    );
                  })}
                </ol>
                {closeBtn}
              </div>
              {body}
              {footer}
            </>
          ) : (
            <>
              <aside className="flex w-[220px] shrink-0 flex-col border-r border-border bg-muted/30 px-4 py-5">
                <DialogTitle className="px-1 text-[17px]">新建项目</DialogTitle>
                <ol className="mt-5 space-y-1" aria-label="步骤">
                  {STEPS.map((s) => {
                    const done = s.n < step;
                    const active = s.n === step;
                    const canJump = s.n <= reached && !active;
                    return (
                      <li key={s.n} aria-current={active ? "step" : undefined}>
                        <button
                          type="button"
                          disabled={!canJump}
                          onClick={() => go(s.n)}
                          className={cn(
                            "flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left disabled:cursor-default",
                            active && "bg-primary/12",
                            canJump && "[@media(hover:hover)]:hover:bg-accent",
                          )}
                        >
                          <span
                            className={cn(
                              "mt-px grid size-5 shrink-0 place-items-center rounded-full text-[11px] tabular-nums",
                              active ? "bg-primary text-primary-foreground" : done ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
                            )}
                          >
                            {done ? <Check className="size-3" /> : s.n}
                          </span>
                          <span className="min-w-0">
                            <span className={cn("block text-[13px]", active ? "font-medium text-foreground" : "text-text-2")}>{s.label}</span>
                            {done && summaries[s.n] && <span className="mt-0.5 block text-[12px] leading-[1.4] text-muted-foreground">{summaries[s.n]}</span>}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              </aside>
              <div className="relative flex min-w-0 flex-1 flex-col">
                <div className="shrink-0 border-b border-border px-6 pt-5 pb-4 pr-12">
                  <h2 className="text-[15px] font-medium">{STEPS[step - 1].label}</h2>
                  <p className="mt-1 text-[12px] text-muted-foreground">{STEPS[step - 1].hint}</p>
                  {closeBtn}
                </div>
                {body}
                {footer}
              </div>
            </>
          )}
        </DialogPrimitive.Popup>
      </DialogPortal>
    </Dialog>
  );
}
