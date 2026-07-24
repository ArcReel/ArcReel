// PROTOTYPE — 变体 B「只读示例项目」。
// 首次进入时小对话框介绍;大厅出现一张可点击的示例项目卡,点入后是
// 只读工作区快照(三栏布局 + warm 横幅 + 全禁用写操作),Figma Community 文件模式。
import { useState } from "react";
import { BookOpen, Landmark, Package, Users, X } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { DemoProjectCard, demoCardPosition, useAnchorRect } from "./prototype-shared";

const NAV = [
  { icon: null, label: "项目概览" },
  { icon: BookOpen, label: "源文件" },
  { icon: Users, label: "角色集" },
  { icon: Landmark, label: "场景库" },
  { icon: Package, label: "道具库" },
];

const SHOTS = Array.from({ length: 9 }, (_, i) => ({
  no: String(i + 1).padStart(2, "0"),
  dur: [4, 3, 5, 4, 6, 3, 4, 5, 4][i],
  hue: [300, 280, 255, 310, 270, 290, 260, 305, 275][i],
}));

function ReadonlyWorkspaceSnapshot({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[9100] flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-label="示例项目(只读演示)"
      style={{ background: "linear-gradient(180deg, var(--color-bg-grad-a), var(--color-bg-grad-b))" }}
    >
      {/* warm 只读横幅 */}
      <div
        className="flex items-center justify-center gap-3 px-4 py-2 text-[12.5px] font-medium"
        style={{
          background: "oklch(0.72 0.13 75 / 0.14)",
          borderBottom: "1px solid oklch(0.72 0.13 75 / 0.4)",
          color: "oklch(0.85 0.1 80)",
        }}
      >
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em]">只读演示</span>
        <span style={{ color: "oklch(0.85 0.1 80 / 0.85)" }}>
          这是预置的示例项目——随便点、随便看,不会产生任何生成任务或费用。
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-2 rounded-full px-2.5 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em]"
          style={{ border: "1px solid oklch(0.72 0.13 75 / 0.5)" }}
        >
          退出演示
        </button>
      </div>

      {/* 简化 GlobalHeader */}
      <div
        className="flex h-12 items-center justify-between border-b border-hairline px-4"
        style={{ background: "oklch(0.18 0.01 265 / 0.7)", backdropFilter: "blur(10px)" }}
      >
        <div className="flex items-center gap-3">
          <button type="button" onClick={onClose} aria-label="返回大厅" className="text-text-3 hover:text-text">
            <X className="h-4 w-4" />
          </button>
          <span className="text-[13.5px] font-semibold text-text">雾都疑云</span>
          <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-accent-2">演示 · DEMO</span>
        </div>
        <div className="flex items-center gap-1.5">
          {["导入", "概述", "资产", "分镜", "视频"].map((phase, i) => (
            <span
              key={phase}
              className="rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.08em]"
              style={
                i <= 3
                  ? { background: "oklch(0.76 0.09 295 / 0.16)", color: "var(--color-accent-2)", border: "1px solid oklch(0.76 0.09 295 / 0.4)" }
                  : { color: "var(--color-text-4)", border: "1px solid var(--color-hairline)" }
              }
            >
              {phase}
            </span>
          ))}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* 左侧资产导航 */}
        <aside className="w-52 shrink-0 space-y-1 border-r border-hairline p-3">
          {NAV.map(({ icon: Icon, label }, i) => (
            <div
              key={label}
              className="flex items-center gap-2.5 rounded-[8px] px-3 py-2 text-[13px]"
              style={
                i === 0
                  ? { background: "oklch(0.76 0.09 295 / 0.14)", color: "var(--color-text)" }
                  : { color: "var(--color-text-3)" }
              }
            >
              {Icon ? <Icon className="h-3.5 w-3.5" aria-hidden /> : <span className="h-3.5 w-3.5" />}
              {label}
            </div>
          ))}
          <div className="pt-3">
            <div className="px-3 font-mono text-[9.5px] font-semibold uppercase tracking-[0.14em] text-text-4">
              分集
            </div>
            {["EP01 迷雾码头", "EP02 旧宅来信", "EP03 双面证人", "EP04 无声告别"].map((ep, i) => (
              <div
                key={ep}
                className="mt-1 rounded-[8px] border border-hairline px-3 py-2 text-[12px] text-text-3"
                style={i === 3 ? { borderColor: "oklch(0.76 0.09 295 / 0.45)", color: "var(--color-text-2)" } : undefined}
              >
                {ep}
              </div>
            ))}
          </div>
        </aside>

        {/* 中间:EP04 分镜画布快照 */}
        <main className="min-w-0 flex-1 overflow-auto p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-text-4">
                EP04 · STORYBOARD
              </div>
              <h2 className="mt-1 text-[18px] font-semibold tracking-tight text-text">无声告别 · 分镜</h2>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled
                title="演示项目为只读,不能触发生成"
                className="arc-btn-secondary cursor-not-allowed text-[12px] opacity-50"
              >
                重新生成分镜
              </button>
              <button
                type="button"
                disabled
                title="演示项目为只读,不能触发生成"
                className="arc-btn-primary cursor-not-allowed text-[12px] opacity-50"
              >
                生成视频
              </button>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            {SHOTS.map((s) => (
              <div key={s.no} className="overflow-hidden rounded-[10px] border border-hairline">
                <div
                  className="relative"
                  style={{
                    aspectRatio: "16 / 9",
                    background: `radial-gradient(80% 110% at 30% 25%, oklch(0.4 0.08 ${s.hue} / 0.6) 0%, transparent 60%), oklch(0.19 0.015 ${s.hue})`,
                  }}
                >
                  <span className="absolute left-2 top-2 font-mono text-[10px] font-semibold text-text-2">
                    SHOT {s.no}
                  </span>
                  <span className="absolute bottom-2 right-2 font-mono text-[10px] text-text-3">{s.dur}s</span>
                </div>
                <div className="px-3 py-2 text-[11.5px] leading-snug text-text-3">
                  {s.no === "01" ? "雨夜,码头远景,汽笛声由远及近" : `镜头 ${s.no} 画面描述(演示数据)`}
                </div>
              </div>
            ))}
          </div>
        </main>
      </div>
    </div>
  );
}

export function VariantDemoProject({ onDone }: { onDone: () => void }) {
  const [stage, setStage] = useState<"intro" | "lobby" | "workspace">("intro");
  const anchorRect = useAnchorRect(stage === "lobby" ? "new-project" : null);

  return (
    <>
      <GlassModal
        open={stage === "intro"}
        onClose={() => {
          setStage("lobby");
        }}
        ariaLabel="示例项目介绍"
        widthClassName="w-full max-w-sm"
      >
        <div className="p-6">
          <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-2">
            WELCOME · DEMO PROJECT
          </div>
          <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-text">
            为你准备了一个示例项目
          </h2>
          <p className="mt-2 text-[13px] leading-relaxed text-text-2">
            「雾都疑云」是一个已经推进到分镜阶段的完整示例:小说原文、AI
            生成的概述、角色、场景和分镜都在里面。它是只读的——随便点开看,不会产生任何生成任务或费用。看完后,新建你自己的项目就是同样的流程。
          </p>
          <div className="mt-5 flex items-center justify-end gap-2">
            <button type="button" onClick={onDone} className="arc-btn-secondary text-[12px]">
              跳过
            </button>
            <button
              type="button"
              onClick={() => setStage("lobby")}
              className="arc-btn-primary text-[12px]"
            >
              去看看示例项目
            </button>
          </div>
        </div>
      </GlassModal>

      {/* 大厅态:示例卡出现在新建卡右侧,带指引光晕 */}
      {stage === "lobby" && anchorRect ? (
        <div className="fixed z-[8900]" style={demoCardPosition(anchorRect)}>
          <DemoProjectCard
            onClick={() => setStage("workspace")}
            style={{ boxShadow: "0 0 0 1.5px var(--color-accent), 0 0 32px -6px oklch(0.76 0.09 295 / 0.6)" }}
          />
          <div className="mt-2 text-center font-mono text-[10.5px] uppercase tracking-[0.12em] text-accent-2">
            点击进入只读示例 ↑
          </div>
        </div>
      ) : null}

      {stage === "workspace" ? (
        <ReadonlyWorkspaceSnapshot
          onClose={() => {
            setStage("lobby");
          }}
        />
      ) : null}
    </>
  );
}
