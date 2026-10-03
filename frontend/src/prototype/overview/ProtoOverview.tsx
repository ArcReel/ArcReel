// PROTOTYPE — 项目概览页与空项目欢迎页原型（#2981），不合并。
// 「四个预设 + 逐轴覆盖」，在工作区根路由上替换 OverviewCanvas。写操作全部是桩。
// 「空项目」「刚建好的广告项目」两种样本是在真实项目数据上清空对应字段的模拟；
// 模拟上传后 2.5 秒「分析完成」，恢复真实的故事设定，用来看首次生成后的切换过程。

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Bot, CheckCircle2, FilePlus2, ImagePlus, Upload } from "lucide-react";
import type { ProjectData } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { prefillAssistant } from "@/components/shared/DraftStatus";
import { useCostStore } from "@/stores/cost-store";
import { OverviewCanvas } from "@/components/canvas/OverviewCanvas";
import { useOverviewProto } from "./store";
import type { AxisState } from "./axes";
import {
  AdBrief,
  AdProducts,
  AssetsBlock,
  CostBlock,
  EpisodesBlock,
  MODE_LABEL,
  OverviewHeader,
  Section,
  StorySetting,
  stub,
} from "./sections";

type Phase = "idle" | "analyzing" | "finishing" | "done";

/** 按样本状态改写真实项目数据 */
function applySample(data: ProjectData, sample: AxisState["sample"], phase: Phase, adDone: boolean): ProjectData {
  if (sample === "empty") {
    const zero = { total: 0, available: 0, stale: 0 };
    return {
      ...data,
      overview: phase === "done" ? data.overview : undefined,
      episodes: [],
      whole_source_files: phase === "idle" ? [] : [{ source_file: "source/原文.txt" }],
      status: data.status
        ? { ...data.status, assets: { character: zero, scene: zero, prop: zero, product: zero } }
        : data.status,
    };
  }
  if (sample === "adNew" && data.content_mode === "ad" && !adDone) {
    return { ...data, products: {}, brief: "" };
  }
  return data;
}

export function ProtoOverviewHost({ projectName, projectData }: { projectName: string; projectData: ProjectData | null }) {
  const { axes } = useOverviewProto();
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);
  useEffect(() => {
    if (projectName) debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  const [phase, setPhase] = useState<Phase>("idle");
  const [adDone, setAdDone] = useState(false);
  const timer = useRef<number | null>(null);
  // 换样本或换项目时回到起点
  useEffect(() => {
    setPhase("idle");
    setAdDone(false);
  }, [axes.sample, projectName]);
  useEffect(() => () => void (timer.current && window.clearTimeout(timer.current)), []);

  const simulateUpload = useCallback(() => {
    stub("打开「分集」的上传对话框，模拟上传了一份整本原文");
    setPhase("analyzing");
    // 「整页切换」：先在欢迎页停 1.2 秒显示「写好了」，再换成概览（现状行为）
    timer.current = window.setTimeout(() => {
      if (axes.transition === "swap") {
        setPhase("finishing");
        timer.current = window.setTimeout(() => setPhase("done"), 1200);
      } else setPhase("done");
    }, 2500);
  }, [axes.transition]);

  const data = useMemo(
    () => (projectData ? applySample(projectData, axes.sample, phase, adDone) : null),
    [projectData, axes.sample, phase, adDone],
  );
  if (!data) return <div className="h-full" aria-busy="true" />;

  if (axes.layout === "legacy") {
    // 现状组件：非真实样本时只读查看，避免现状的上传入口写进真实项目
    return (
      <div className="h-full" inert={axes.sample !== "real" ? true : undefined}>
        <OverviewCanvas projectName={projectName} projectData={data} />
      </div>
    );
  }

  return (
    <ProtoOverview
      data={data}
      axes={axes}
      phase={phase}
      onUploadSource={simulateUpload}
      onAdInitDone={() => {
        stub("建商品、上传商品图、写入创作灵感");
        setAdDone(true);
      }}
      sampleMismatch={axes.sample === "adNew" && projectData?.content_mode !== "ad"}
    />
  );
}

function Page({ children, wide, className = "" }: { children: ReactNode; wide?: boolean; className?: string }) {
  return (
    <div className="@container relative h-full overflow-y-auto [scrollbar-gutter:stable]">
      <div className={`px-6 py-6 xl:px-8 ${wide ? "" : "max-w-[calc(760px+4rem)]"} ${className}`}>{children}</div>
    </div>
  );
}

function ProtoOverview({
  data,
  axes,
  phase,
  onUploadSource,
  onAdInitDone,
  sampleMismatch,
}: {
  data: ProjectData;
  axes: AxisState;
  phase: Phase;
  onUploadSource: () => void;
  onAdInitDone: () => void;
  sampleMismatch: boolean;
}) {
  const isAd = data.content_mode === "ad";
  const emptyProject = !isAd && !data.overview && (data.episodes?.length ?? 0) === 0;
  const showAdInit = isAd && Object.keys(data.products ?? {}).length === 0 && !(data.brief ?? "").trim();

  if (sampleMismatch) {
    return (
      <Page>
        <p className="text-[14px] text-muted-foreground">「刚建好的广告项目」样本只对广告项目生效，请用项目切换器切到「重生之我是广告之神」。</p>
      </Page>
    );
  }

  if (showAdInit && axes.adInit === "form") {
    return (
      <Page>
        <OverviewHeader data={data} mode={axes.header === "none" ? "title" : axes.header} />
        <AdInitForm onDone={onAdInitDone} />
      </Page>
    );
  }

  // 独立欢迎页：空闲时；「整页切换」方式下分析中与完成也留在欢迎页
  const welcomeOwnsPhase = axes.transition === "swap" ? phase !== "done" : phase === "idle";
  if (emptyProject && axes.empty !== "inline" && welcomeOwnsPhase) {
    return (
      <Page className="@container">
        {axes.empty === "welcome" ? (
          <WelcomePage title={data.title} phase={phase} onUpload={onUploadSource} />
        ) : (
          <StartPage data={data} phase={phase} onUpload={onUploadSource} />
        )}
      </Page>
    );
  }

  const analyzing = phase === "analyzing";
  const setting = (bare?: boolean, collapsedSummary?: boolean) => isAd && axes.adStory === "tab" ? null : (
    <StorySetting
      data={data}
      fields={axes.fields}
      analyzing={analyzing}
      emptyProject={emptyProject}
      onUploadSource={onUploadSource}
      bare={bare}
      collapsedSummary={collapsedSummary}
    />
  );
  const adBlocks = isAd && (
    <>
      <AdBrief data={data} duration={axes.adDuration} />
      {axes.adInit === "inline" && <AdProducts data={data} />}
    </>
  );

  if (axes.layout === "dashboard") {
    return (
      <Page wide>
        <div className="grid grid-cols-1 gap-5 @[1040px]:grid-cols-[minmax(0,1fr)_minmax(340px,400px)] @[1040px]:items-start">
          <div className="min-w-0 space-y-5">
            <OverviewHeader data={data} mode={axes.header} />
            {adBlocks}
            {setting()}
          </div>
          <aside className="@container min-w-0 space-y-5 @[1040px]:pt-1" aria-label="制作">
            <AssetsBlock data={data} mode={axes.assets} />
            {axes.cost === "full" ? (
              <CostBlock mode="full" />
            ) : (
              <div className="rounded-[10px] border border-border bg-card px-5 py-3">
                <CostBlock mode={axes.cost} />
              </div>
            )}
            <div className="@container">
              <EpisodesBlock data={data} mode={axes.episodeList === "cards" ? "compact" : axes.episodeList} />
            </div>
          </aside>
        </div>
      </Page>
    );
  }

  if (axes.layout === "episodes") {
    return (
      <Page wide>
        <div className="space-y-6">
          <OverviewHeader data={data} mode={axes.header} />
          {adBlocks}
          {setting(false, true)}
          <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
            <AssetsBlock data={data} mode={axes.assets === "bars" ? "inline" : axes.assets} />
            <CostBlock mode={axes.cost === "full" ? "summary" : axes.cost} />
          </div>
          <div className="@container">
            <EpisodesBlock data={data} mode={axes.episodeList === "none" ? "cards" : axes.episodeList} />
          </div>
        </div>
      </Page>
    );
  }

  // bible：单列限宽
  return (
    <Page>
      <div className="@container space-y-5">
        <OverviewHeader data={data} mode={axes.header} />
        {(axes.assets === "inline" || axes.cost === "summary" || axes.cost === "none") && (
          <div className="space-y-1.5">
            <AssetsBlock data={data} mode={axes.assets === "inline" ? "inline" : "none"} />
            {axes.cost !== "full" && <CostBlock mode={axes.cost} />}
          </div>
        )}
        {adBlocks}
        {setting()}
        {axes.assets === "bars" && <AssetsBlock data={data} mode="bars" />}
        {axes.cost === "full" && <CostBlock mode="full" />}
        <EpisodesBlock data={data} mode={axes.episodeList} />
      </div>
    </Page>
  );
}

// ---------------------------------------------------------------------------
// 空项目：独立欢迎页与开始页
// ---------------------------------------------------------------------------

function useDropzone(onUpload: () => void) {
  const [dragging, setDragging] = useState(false);
  return {
    dragging,
    props: {
      onDragOver: (e: React.DragEvent) => {
        e.preventDefault();
        setDragging(true);
      },
      onDragLeave: () => setDragging(false),
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        setDragging(false);
        onUpload();
      },
      onClick: onUpload,
    },
  };
}

function PhaseCard({ phase }: { phase: Phase }) {
  if (phase === "analyzing" || phase === "idle") {
    return (
      <div role="status" aria-live="polite" className="rounded-[10px] border border-border bg-card px-6 py-10 text-center">
        <span className="mx-auto block size-2 animate-pulse rounded-full bg-primary" aria-hidden />
        <p className="mt-3 text-[15px] font-medium text-foreground">正在读取原文</p>
        <p className="mt-1 text-[13px] text-muted-foreground">AI 在写梗概、类型、主题与世界观，通常需要几十秒。</p>
      </div>
    );
  }
  return (
    <div role="status" aria-live="polite" className="rounded-[10px] border border-border bg-card px-6 py-10 text-center">
      <CheckCircle2 className="mx-auto size-6 text-[var(--color-good)]" />
      <p className="mt-3 text-[15px] font-medium text-foreground">故事设定写好了</p>
    </div>
  );
}

function WelcomePage({ title, phase, onUpload }: { title: string; phase: Phase; onUpload: () => void }) {
  const dz = useDropzone(onUpload);
  return (
    <div className="mx-auto max-w-[560px] space-y-6 pt-[8dvh]">
      <header>
        <h1 className="display-serif text-[26px] font-semibold tracking-tight text-foreground">开始制作《{title}》</h1>
        <p className="mt-2 text-[14px] leading-relaxed text-[var(--color-text-2)]">
          放进小说或剧本，AI 会先读出故事设定，再帮你规划分集。
        </p>
      </header>
      {phase === "idle" ? (
        <>
          <button
            type="button"
            {...dz.props}
            className={`focus-ring flex w-full flex-col items-center gap-2 rounded-[10px] border border-dashed px-6 py-12 text-center transition-colors ${
              dz.dragging ? "border-primary bg-primary/10" : "border-[var(--color-hairline-strong)] hover:border-primary/50 hover:bg-primary/5"
            }`}
          >
            <Upload className="size-6 text-muted-foreground" />
            <span className="text-[15px] font-medium text-foreground">拖入原文，或点击选择文件</span>
            <span className="text-[13px] text-muted-foreground">TXT、MD、DOCX、EPUB、PDF，可以一次放多个</span>
          </button>
          <p className="text-[13px] text-muted-foreground">
            没有原文？在「分集」里新建一集从空白开始写，或者在右侧把构想告诉 Agent。
          </p>
        </>
      ) : (
        <PhaseCard phase={phase} />
      )}
    </div>
  );
}

function StartPage({ data, phase, onUpload }: { data: ProjectData; phase: Phase; onUpload: () => void }) {
  const dz = useDropzone(onUpload);
  const card =
    "focus-ring flex flex-col items-start gap-2 rounded-[10px] border border-border bg-card p-5 text-left transition-colors hover:border-[var(--color-hairline-strong)] hover:bg-secondary/30";
  return (
    <div className="max-w-[960px] space-y-6">
      <header>
        <h1 className="display-serif text-[26px] font-semibold tracking-tight text-foreground">{data.title}</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">{MODE_LABEL[data.content_mode]} · 选一种方式开始</p>
      </header>
      {phase === "idle" ? (
        <div className="grid grid-cols-1 gap-3 @[720px]:grid-cols-3">
          <button type="button" {...dz.props} className={`${card} ${dz.dragging ? "border-primary bg-primary/10" : ""} border-dashed`}>
            <Upload className="size-5 text-primary" />
            <span className="text-[15px] font-medium text-foreground">上传原文</span>
            <span className="text-[13px] leading-relaxed text-muted-foreground">
              拖入小说或剧本。AI 先读出故事设定，再按原文规划分集。
            </span>
          </button>
          <button type="button" className={card} onClick={() => stub("跳到「分集」并打开新建一集")}>
            <FilePlus2 className="size-5 text-muted-foreground" />
            <span className="text-[15px] font-medium text-foreground">新建空白集</span>
            <span className="text-[13px] leading-relaxed text-muted-foreground">没有原文，自己一集一集写脚本。</span>
          </button>
          <button
            type="button"
            className={card}
            onClick={() => prefillAssistant("我想做一部短剧，大致构想是：")}
          >
            <Bot className="size-5 text-muted-foreground" />
            <span className="text-[15px] font-medium text-foreground">和 Agent 聊构想</span>
            <span className="text-[13px] leading-relaxed text-muted-foreground">只有一个点子？让 Agent 帮你写成故事和分集。</span>
          </button>
        </div>
      ) : (
        <div className="max-w-[560px]">
          <PhaseCard phase={phase} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 广告初始化表单（现状精简）
// ---------------------------------------------------------------------------

function AdInitForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [brief, setBrief] = useState("");
  const [files, setFiles] = useState(0);
  const [sheet, setSheet] = useState(false);
  const productDirty = name.trim() !== "" || desc.trim() !== "" || files > 0 || sheet;
  const productOk = name.trim() !== "" && (!sheet || desc.trim() !== "");
  const canSubmit = productDirty ? productOk : brief.trim() !== "";
  const missing = [
    productDirty && !name.trim() && "商品名称",
    productDirty && sheet && !desc.trim() && "商品描述",
    !productDirty && !brief.trim() && "创作灵感或商品",
  ].filter(Boolean);
  const label = "text-[13px] text-[var(--color-text-2)]";
  return (
    <div className="mt-6 space-y-6">
      <p className="text-[14px] leading-relaxed text-[var(--color-text-2)]">
        先告诉 AI 要拍什么。有商品就填商品；只想做创意短片，写创作灵感就够了。
      </p>
      <Section title="商品（可选）">
        <div className="space-y-4">
          <label className="block">
            <span className={label}>商品名称</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} className="mt-1.5 h-8 text-[14px] md:text-[14px]" />
          </label>
          <div>
            <span className={label}>商品图</span>
            <button
              type="button"
              onClick={() => setFiles((n) => n + 1)}
              className="focus-ring mt-1.5 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline-strong)] px-3 py-4 text-[13px] text-muted-foreground hover:border-primary/50"
            >
              <ImagePlus className="size-4" />
              {files > 0 ? `已选 ${files} 张，点击再加` : "拖入或点击选择 · PNG、JPG、WebP，可多张"}
            </button>
          </div>
          <label className="block">
            <span className={label}>商品描述</span>
            <Textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="外观、材质、卖点"
              className="mt-1.5 resize-none text-[14px] md:text-[14px]"
            />
          </label>
          <label className="flex items-start gap-2">
            <Checkbox checked={sheet} onCheckedChange={(v) => setSheet(v === true)} className="mt-0.5" />
            <span>
              <span className={label}>同时生成商品资产图</span>
              <span className="block text-[13px] text-muted-foreground">按描述生成一张标准图，需要先填商品描述。</span>
            </span>
          </label>
        </div>
      </Section>
      <label className="block">
        <span className={label}>创作灵感</span>
        <Textarea
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          placeholder="想拍什么、给谁看、什么调性"
          className="mt-1.5 min-h-24 resize-none text-[14px] md:text-[14px]"
        />
      </label>
      <div className="flex items-center justify-end gap-3">
        {!canSubmit && missing.length > 0 && <span className="text-[13px] text-muted-foreground">还需要：{missing.join("、")}</span>}
        <Button disabled={!canSubmit} onClick={onDone}>
          开始制作
        </Button>
      </div>
    </div>
  );
}
