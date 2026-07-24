// PROTOTYPE — 变体 C「分步演示面板」。
// 居中大玻璃 modal:左列是"制片工序表"(mono 序号 + 竖向进度线),右侧图文演示区。
// 内容与应用运行时状态完全解耦(VS Code Walkthrough 模式),零副作用天然成立。
// 右侧插图为 CSS 自绘占位——正式实现时替换为真实界面截图/动图素材。
import { useState, type ReactNode } from "react";
import { GlassModal } from "@/components/ui/GlassModal";

interface PanelStep {
  no: string;
  nav: string;
  title: string;
  body: string;
  Figure: () => ReactNode;
}

/* ---------- CSS 自绘插图(正式版换成截图/动图) ---------- */

function FigFrame({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="relative grid h-full w-full place-items-center overflow-hidden rounded-[10px] border border-hairline"
      style={{
        background:
          "radial-gradient(100% 130% at 20% 10%, oklch(0.3 0.05 295 / 0.35) 0%, transparent 55%), oklch(0.17 0.01 265)",
      }}
    >
      {children}
    </div>
  );
}

function FigProvider() {
  return (
    <FigFrame>
      <div className="w-3/5 space-y-2.5">
        {["Gemini", "火山方舟", "Vidu"].map((name, i) => (
          <div
            key={name}
            className="flex items-center justify-between rounded-[8px] border border-hairline px-3 py-2"
            style={i === 0 ? { borderColor: "oklch(0.76 0.09 295 / 0.5)", background: "oklch(0.76 0.09 295 / 0.08)" } : undefined}
          >
            <span className="text-[12px] text-text-2">{name}</span>
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: i === 0 ? "var(--color-good)" : "var(--color-hairline-strong)" }}
            />
          </div>
        ))}
        <div className="flex items-center gap-2 rounded-[8px] border border-dashed border-hairline px-3 py-2">
          <span className="font-mono text-[11px] text-text-4">API KEY</span>
          <span className="h-2 flex-1 rounded-[2px] bg-hairline/70" />
        </div>
      </div>
    </FigFrame>
  );
}

function FigImport() {
  return (
    <FigFrame>
      <div className="grid w-3/5 place-items-center rounded-[10px] border border-dashed p-6" style={{ borderColor: "oklch(0.76 0.09 295 / 0.5)" }}>
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-accent-2">novel.txt</span>
        <span className="mt-2 text-[11.5px] text-text-3">拖入小说原文 · txt / docx / epub / pdf</span>
        <span className="mt-3 rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.1em]" style={{ background: "oklch(0.76 0.09 295 / 0.16)", color: "var(--color-accent-2)" }}>
          AI 通读全文 → 概述 + 分集大纲
        </span>
      </div>
    </FigFrame>
  );
}

function FigAssets() {
  return (
    <FigFrame>
      <div className="flex gap-3">
        {[
          ["角色", "12"],
          ["场景", "9"],
          ["道具", "7"],
        ].map(([label, n]) => (
          <div key={label} className="w-24 overflow-hidden rounded-[8px] border border-hairline">
            <div
              className="grid place-items-center"
              style={{ aspectRatio: "3 / 4", background: "radial-gradient(80% 80% at 40% 30%, oklch(0.36 0.07 300 / 0.5), transparent 60%), oklch(0.2 0.015 275)" }}
            >
              <span className="font-mono text-[16px] font-semibold text-text-2">{n}</span>
            </div>
            <div className="py-1.5 text-center text-[11px] text-text-3">{label}</div>
          </div>
        ))}
      </div>
    </FigFrame>
  );
}

function FigStoryboard() {
  return (
    <FigFrame>
      <div className="grid w-4/5 grid-cols-4 gap-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="rounded-[6px] border border-hairline"
            style={{
              aspectRatio: "16 / 9",
              background: `radial-gradient(80% 100% at 30% 20%, oklch(0.38 0.07 ${255 + i * 8} / 0.55), transparent 60%), oklch(0.19 0.012 270)`,
            }}
          />
        ))}
      </div>
    </FigFrame>
  );
}

function FigVideo() {
  return (
    <FigFrame>
      <div className="w-3/5 overflow-hidden rounded-[10px] border border-hairline">
        <div className="relative" style={{ aspectRatio: "16 / 9", background: "radial-gradient(90% 120% at 30% 20%, oklch(0.42 0.09 300 / 0.6), transparent 60%), oklch(0.18 0.015 280)" }}>
          <span className="absolute inset-0 m-auto grid h-11 w-11 place-items-center rounded-full" style={{ background: "oklch(0 0 0 / 0.5)", border: "1px solid oklch(1 0 0 / 0.35)" }}>
            <span className="ml-0.5 inline-block border-y-[7px] border-l-[11px] border-y-transparent" style={{ borderLeftColor: "oklch(0.95 0 0)" }} />
          </span>
        </div>
        <div className="flex items-center gap-1 px-2 py-1.5">
          {Array.from({ length: 14 }).map((_, i) => (
            <span key={i} className="h-3 w-1 rounded-[1px]" style={{ background: i < 9 ? "oklch(0.76 0.09 295 / 0.7)" : "var(--color-hairline)" }} />
          ))}
        </div>
      </div>
    </FigFrame>
  );
}

function FigExport() {
  return (
    <FigFrame>
      <div className="flex flex-col items-center gap-3">
        <div className="flex gap-2.5">
          {["成片 MP4", "剪映草稿", "项目 ZIP"].map((x) => (
            <span key={x} className="rounded-[8px] border border-hairline px-3 py-2 text-[11.5px] text-text-2">
              {x}
            </span>
          ))}
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-4">EXPORT · 随时导出,随时回滚版本</span>
      </div>
    </FigFrame>
  );
}

/* ---------- 步骤定义 ---------- */

const STEPS: PanelStep[] = [
  {
    no: "01",
    nav: "配置供应商",
    title: "先接上模型供应商",
    body: "生成剧本、图像与视频需要至少一家供应商的 API Key(Gemini、火山方舟、Vidu 等)。在设置页粘贴 Key、点测试连接,绿灯亮起就绪。没配置时顶栏设置图标会有角标提醒。",
    Figure: FigProvider,
  },
  {
    no: "02",
    nav: "导入小说",
    title: "新建项目,丢进一部小说",
    body: "支持 txt / docx / epub / pdf。AI 会通读全文,生成故事概述与分集大纲——这是后面一切生成的底稿,你可以随时修改。",
    Figure: FigImport,
  },
  {
    no: "03",
    nav: "生成资产",
    title: "角色、场景、道具一次成型",
    body: "AI 从原文提取角色、场景和道具,逐个生成造型图。它们是全剧统一的\"选角与美术\":后续所有分镜都引用同一套资产,保证形象不漂移。",
    Figure: FigAssets,
  },
  {
    no: "04",
    nav: "分镜",
    title: "剧本拆成一格格分镜",
    body: "每集剧本被拆成镜头,逐镜生成分镜图。不满意的单镜可以改提示词重新生成,也可以整集重来——版本历史都留着,随时回滚。",
    Figure: FigStoryboard,
  },
  {
    no: "05",
    nav: "视频",
    title: "分镜图动起来",
    body: "以分镜图为首帧生成视频片段,自动拼接配音与字幕。任务进队列后台跑,你可以同时推进其他集。",
    Figure: FigVideo,
  },
  {
    no: "06",
    nav: "导出",
    title: "成片、剪映草稿,随你",
    body: "导出成片 MP4,或导出剪映草稿继续精剪。到这里,一部小说就成了一部短剧。现在新建一个项目,亲手走一遍——本演示随时可从设置页重看。",
    Figure: FigExport,
  },
];

export function VariantPanel({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const current = STEPS[step];
  const { Figure } = current;

  return (
    <GlassModal
      open
      onClose={onDone}
      labelledBy="onboarding-panel-title"
      widthClassName="w-full max-w-3xl"
      closeOnBackdrop={false}
    >
      <div className="flex" style={{ minHeight: 440 }}>
        {/* 左列:制片工序表 */}
        <nav className="w-48 shrink-0 border-r border-hairline p-5" aria-label="演示步骤">
          <div className="font-mono text-[9.5px] font-semibold uppercase tracking-[0.18em] text-text-4">
            ArcReel · 创作工序
          </div>
          <ol className="relative mt-4 space-y-1">
            <span
              aria-hidden
              className="absolute bottom-3 left-[13px] top-3 w-px"
              style={{ background: "var(--color-hairline)" }}
            />
            {STEPS.map((s, i) => (
              <li key={s.no} className="relative">
                <button
                  type="button"
                  onClick={() => setStep(i)}
                  className="flex w-full items-center gap-2.5 rounded-[8px] px-1.5 py-1.5 text-left"
                  aria-current={i === step ? "step" : undefined}
                >
                  <span
                    className="relative z-10 grid h-6 w-6 shrink-0 place-items-center rounded-full font-mono text-[10px] font-semibold"
                    style={
                      i === step
                        ? { background: "var(--color-accent)", color: "oklch(0.15 0.01 265)" }
                        : i < step
                          ? { background: "oklch(0.76 0.09 295 / 0.2)", color: "var(--color-accent-2)", border: "1px solid oklch(0.76 0.09 295 / 0.5)" }
                          : { background: "var(--color-surface-2, oklch(0.2 0.01 265))", color: "var(--color-text-4)", border: "1px solid var(--color-hairline)" }
                    }
                  >
                    {s.no}
                  </span>
                  <span
                    className="text-[12.5px]"
                    style={{ color: i === step ? "var(--color-text)" : "var(--color-text-3)" }}
                  >
                    {s.nav}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </nav>

        {/* 右侧:演示区 */}
        <div className="flex min-w-0 flex-1 flex-col p-6">
          <div style={{ height: 210 }}>
            <Figure />
          </div>
          <div className="mt-4 font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-2">
            STEP {current.no} / 0{STEPS.length}
          </div>
          <h2 id="onboarding-panel-title" className="mt-1.5 text-[19px] font-semibold tracking-tight text-text">
            {current.title}
          </h2>
          <p className="mt-2 text-[13px] leading-relaxed text-text-2">{current.body}</p>
          <div className="mt-auto flex items-center justify-between pt-5">
            <button type="button" onClick={onDone} className="text-[12px] text-text-4 hover:text-text-2">
              跳过演示
            </button>
            <div className="flex items-center gap-2">
              {step > 0 ? (
                <button
                  type="button"
                  onClick={() => setStep((s) => s - 1)}
                  className="arc-btn-secondary text-[12px]"
                >
                  上一步
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => (step === STEPS.length - 1 ? onDone() : setStep((s) => s + 1))}
                className="arc-btn-primary text-[12px]"
              >
                {step === STEPS.length - 1 ? "开始创作" : "下一步"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </GlassModal>
  );
}
