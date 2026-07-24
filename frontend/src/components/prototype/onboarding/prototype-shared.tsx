// PROTOTYPE — onboarding 形态对比原型的共享小件(切换条 / 演示项目卡)。
// 本目录全部为一次性原型代码:不进 main、不接 i18n、不写测试。
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "wouter";

export type OnboardingVariant = "tour" | "demo-project" | "panel";

export const VARIANTS: { key: OnboardingVariant; label: string }[] = [
  { key: "tour", label: "A — 聚光灯 Tour 遮罩" },
  { key: "demo-project", label: "B — 只读示例项目" },
  { key: "panel", label: "C — 分步演示面板" },
];

export function useVariant(): [OnboardingVariant, (v: OnboardingVariant) => void] {
  const [, navigate] = useLocation();
  const read = (): OnboardingVariant => {
    const raw = new URLSearchParams(window.location.search).get("variant");
    return raw === "demo-project" || raw === "panel" ? raw : "tour";
  };
  const [variant, setVariant] = useState<OnboardingVariant>(read);
  const set = useCallback(
    (v: OnboardingVariant) => {
      const params = new URLSearchParams(window.location.search);
      params.set("variant", v);
      navigate(`${window.location.pathname}?${params.toString()}`, { replace: true });
      setVariant(v);
    },
    [navigate],
  );
  return [variant, set];
}

/** 浮动切换条:← A/B/C →。与被评估的设计明显区分,DEV only(入口处已 gate)。 */
export function PrototypeSwitcher({
  variant,
  onChange,
  onReplay,
}: {
  variant: OnboardingVariant;
  onChange: (v: OnboardingVariant) => void;
  onReplay: () => void;
}) {
  const idx = VARIANTS.findIndex((v) => v.key === variant);
  const cycle = useCallback(
    (delta: number) => {
      const next = VARIANTS[(idx + delta + VARIANTS.length) % VARIANTS.length];
      onChange(next.key);
    },
    [idx, onChange],
  );
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (
        el instanceof HTMLElement &&
        (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)
      ) {
        return;
      }
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [cycle]);
  return (
    <div
      className="fixed bottom-4 left-1/2 z-[9999] flex -translate-x-1/2 items-center gap-1 rounded-full px-2 py-1.5"
      style={{
        background: "oklch(0.98 0 0)",
        color: "oklch(0.2 0 0)",
        boxShadow: "0 6px 24px -6px oklch(0 0 0 / 0.6)",
        fontFamily: "var(--font-mono)",
        fontSize: "12px",
      }}
    >
      <button
        type="button"
        onClick={() => cycle(-1)}
        aria-label="上一个变体"
        className="grid h-7 w-7 place-items-center rounded-full hover:bg-black/10"
      >
        ←
      </button>
      <span className="px-2 font-semibold">{VARIANTS[idx].label}</span>
      <button
        type="button"
        onClick={() => cycle(1)}
        aria-label="下一个变体"
        className="grid h-7 w-7 place-items-center rounded-full hover:bg-black/10"
      >
        →
      </button>
      <span className="mx-1 h-4 w-px bg-black/20" />
      <button
        type="button"
        onClick={onReplay}
        className="rounded-full px-2 py-1 font-semibold hover:bg-black/10"
      >
        重播
      </button>
    </div>
  );
}

/** 锚定某个 data-onboarding 元素的实时 rect(简化版:监听 resize,tour 期间页面不滚动)。 */
export function useAnchorRect(anchor: string | null): DOMRect | null {
  const [rect, setRect] = useState<DOMRect | null>(null);
  useEffect(() => {
    if (!anchor) return;
    const measure = () => {
      const el = document.querySelector(`[data-onboarding="${anchor}"]`);
      setRect(el ? el.getBoundingClientRect() : null);
    };
    // 锚点可能在视口外(如非空大厅时新建卡在网格末尾),先滚到视野中央再测量
    const raf = requestAnimationFrame(() => {
      document
        .querySelector(`[data-onboarding="${anchor}"]`)
        ?.scrollIntoView({ block: "center", behavior: "instant" as ScrollBehavior });
      measure();
    });
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [anchor]);
  return anchor ? rect : null;
}

const DEMO_EPISODE_BARS = [1, 1, 1, 0.6, 0.25, 0, 0, 0];

/** 演示卡放在锚点(新建卡)旁边:右侧放不下就放左侧(非空大厅时新建卡常在行尾)。 */
export function demoCardPosition(rect: DOMRect): React.CSSProperties {
  const fitsRight = rect.right + 16 + rect.width <= window.innerWidth - 8;
  return {
    left: fitsRight ? rect.right + 16 : rect.left - 16 - rect.width,
    top: rect.top,
    width: rect.width,
  };
}

/**
 * 自绘的「演示项目卡」:模拟一张已推进到分镜阶段的真实项目卡。
 * 变体 A 用作静态展示(demo 数据注入的效果),变体 B 用作可点击的示例项目入口。
 */
export function DemoProjectCard({
  onClick,
  style,
}: {
  onClick?: () => void;
  style?: React.CSSProperties;
}) {
  const stats = useMemo(
    () => [
      ["分集", "8"],
      ["角色", "12"],
      ["场景", "9"],
      ["分镜", "64"],
    ],
    [],
  );
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className="group relative flex w-full flex-col overflow-hidden rounded-[12px] border border-hairline text-left"
      style={{
        background: "linear-gradient(180deg, oklch(0.21 0.012 270 / 0.9), oklch(0.18 0.01 265 / 0.92))",
        boxShadow: "0 18px 40px -28px oklch(0 0 0 / 0.8)",
        ...style,
      }}
    >
      <span
        className="absolute left-3 top-3 z-10 rounded-full px-2 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.14em]"
        style={{
          background: "oklch(0.76 0.09 295 / 0.18)",
          border: "1px solid oklch(0.76 0.09 295 / 0.5)",
          color: "var(--color-accent-2)",
        }}
      >
        演示 · DEMO
      </span>
      <div
        className="relative"
        style={{
          aspectRatio: "2 / 1",
          background:
            "radial-gradient(90% 120% at 25% 20%, oklch(0.42 0.09 300 / 0.55) 0%, transparent 55%), radial-gradient(70% 90% at 85% 90%, oklch(0.35 0.07 250 / 0.5) 0%, transparent 60%), oklch(0.2 0.02 280)",
        }}
      >
        <span
          className="absolute bottom-2.5 right-3 font-mono text-[10px] uppercase tracking-[0.12em]"
          style={{ color: "oklch(0.9 0.02 295 / 0.75)" }}
        >
          雾都疑云 · EP04
        </span>
      </div>
      <div className="space-y-2.5 px-4 pb-3.5 pt-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[14.5px] font-semibold tracking-tight text-text">雾都疑云</span>
          <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-text-3">悬疑 · 民国</span>
        </div>
        <span
          className="inline-block rounded-full px-2 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.12em]"
          style={{
            background: "oklch(0.72 0.15 155 / 0.14)",
            border: "1px solid oklch(0.72 0.15 155 / 0.4)",
            color: "var(--color-good)",
          }}
        >
          分镜生成中
        </span>
        <div className="flex gap-[3px]">
          {DEMO_EPISODE_BARS.map((v, i) => (
            <span
              key={i}
              className="h-[3px] flex-1 rounded-[1.5px]"
              style={{
                background:
                  v > 0
                    ? `oklch(0.76 0.09 295 / ${0.35 + v * 0.55})`
                    : "var(--color-hairline)",
              }}
            />
          ))}
        </div>
        <div
          className="grid grid-cols-4 overflow-hidden rounded-[7px] border border-hairline"
          style={{ background: "oklch(0.16 0.01 265 / 0.5)" }}
        >
          {stats.map(([label, value], i) => (
            <div
              key={label}
              className={`px-1.5 py-2 text-center${i < 3 ? " border-r border-hairline" : ""}`}
            >
              <div className="font-mono text-[12px] font-semibold text-text-2">{value}</div>
              <div className="mt-0.5 text-[9px] text-text-4">{label}</div>
            </div>
          ))}
        </div>
      </div>
    </Tag>
  );
}
