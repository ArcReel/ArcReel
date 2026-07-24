// PROTOTYPE — 变体 A「聚光灯 Tour 遮罩 + demo 数据」。
// 暗化遮罩高亮大厅真实元素,气泡分步讲解;第 3 步在新建卡旁注入一张演示项目卡,
// 展示「tour + demo 数据」组合下能讲到的深度。零副作用:不触发任何真实操作。
import { useState } from "react";
import { DemoProjectCard, demoCardPosition, useAnchorRect } from "./prototype-shared";

interface TourStep {
  anchor: string | null; // data-onboarding 锚点;null = 居中气泡
  kicker: string;
  title: string;
  body: string;
  showDemoCard?: boolean;
}

const STEPS: TourStep[] = [
  {
    anchor: null,
    kicker: "WELCOME · 01",
    title: "欢迎来到 ArcReel",
    body: "把一部小说变成一部短剧:导入原文,AI 生成剧本、角色、分镜和成片。接下来 4 步带你认识创作台——全程只是演示,不会创建任何真实数据。",
  },
  {
    anchor: "new-project",
    kicker: "CREATE · 02",
    title: "一切从新建项目开始",
    body: "点这里创建项目并导入小说原文(txt / docx / epub / pdf)。AI 会先通读全文,生成故事概述和分集大纲。",
  },
  {
    anchor: "new-project",
    kicker: "PREVIEW · 03",
    title: "项目推进后长这样",
    body: "旁边这张是演示卡:海报、分集进度、角色与分镜统计一目了然。从概述到成片的每一步都在项目工作台里完成。",
    showDemoCard: true,
  },
  {
    anchor: "settings",
    kicker: "SETUP · 04",
    title: "先配置好模型供应商",
    body: "生成剧本、图像和视频需要至少一家供应商的 API Key。这个角标亮着,说明还有配置没完成——点击设置即可前往。",
  },
  {
    anchor: null,
    kicker: "READY · 05",
    title: "轮到你了",
    body: "创作链路:导入小说 → 生成概述与资产 → 分镜 → 视频 → 导出成片或剪映草稿。现在就新建一个项目试试;想重看本引导,设置页里随时可以再打开。",
  },
];

const BUBBLE_W = 340;

function bubblePosition(rect: DOMRect | null): React.CSSProperties {
  if (!rect) {
    return { left: "50%", top: "50%", transform: "translate(-50%, -50%)" };
  }
  // 优先放在锚点下方,越界则放上方;水平方向夹在视口内
  const left = Math.min(Math.max(rect.left, 16), window.innerWidth - BUBBLE_W - 16);
  const below = rect.bottom + 14;
  if (below + 220 < window.innerHeight) return { left, top: below };
  return { left, top: Math.max(rect.top - 14 - 220, 16) };
}

export function VariantTour({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const current = STEPS[step];
  const rect = useAnchorRect(current.anchor);
  const demoAnchorRect = useAnchorRect(current.showDemoCard ? "new-project" : null);

  return (
    <div className="fixed inset-0 z-[9000]" role="dialog" aria-modal="true" aria-label="新手引导">
      {/* 聚光灯:高亮框以外全部暗化 */}
      {rect ? (
        <div
          className="absolute rounded-[14px] transition-all duration-300"
          style={{
            left: rect.left - 6,
            top: rect.top - 6,
            width: rect.width + 12,
            height: rect.height + 12,
            border: "1.5px solid var(--color-accent)",
            boxShadow:
              "0 0 0 9999px oklch(0 0 0 / 0.72), 0 0 24px -4px var(--color-accent-glow, oklch(0.76 0.09 295 / 0.7))",
          }}
        />
      ) : (
        <div className="absolute inset-0" style={{ background: "oklch(0 0 0 / 0.72)" }} />
      )}

      {/* demo 数据注入:演示项目卡出现在新建卡右侧一格 */}
      {current.showDemoCard && demoAnchorRect ? (
        <div className="absolute" style={demoCardPosition(demoAnchorRect)}>
          <DemoProjectCard />
        </div>
      ) : null}

      {/* 步骤气泡 */}
      <div
        className="arc-glass-panel absolute overflow-hidden rounded-2xl p-5"
        style={{ width: BUBBLE_W, ...bubblePosition(current.showDemoCard && demoAnchorRect ? demoAnchorRect : rect) }}
      >
        <span aria-hidden="true" className="arc-glass-hairline" data-tone="accent" />
        <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-2">
          {current.kicker}
        </div>
        <h2 className="mt-2 text-[17px] font-semibold tracking-tight text-text">{current.title}</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-text-2">{current.body}</p>
        <div className="mt-4 flex items-center justify-between">
          <div className="flex gap-1.5" aria-label={`第 ${step + 1} 步,共 ${STEPS.length} 步`}>
            {STEPS.map((_, i) => (
              <span
                key={i}
                className="h-1.5 w-1.5 rounded-full transition-colors"
                style={{
                  background: i === step ? "var(--color-accent)" : "var(--color-hairline-strong)",
                }}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onDone} className="arc-btn-secondary text-[12px]">
              跳过
            </button>
            {step > 0 ? (
              <button
                type="button"
                onClick={() => setStep((s) => Math.max(0, s - 1))}
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
  );
}
