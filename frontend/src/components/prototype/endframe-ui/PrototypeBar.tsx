// PROTOTYPE — throwaway code for issue #1276 (尾帧设置 UI 交互原型)。
// 不进 main：变体拍板后本文件整体丢弃，仅保留胜出方案的重写实现。
import { useEffect, type ReactNode } from "react";
import { useLocation, useSearch } from "wouter";
import { ChevronLeft, ChevronRight, FlaskConical } from "lucide-react";

export const PROTOTYPE_ENABLED = import.meta.env.DEV;

/** 读当前 URL 上的原型变体键；缺省取第一个变体 */
export function usePrototypeVariant(param: string, variants: string[]): string {
  const search = useSearch();
  const v = new URLSearchParams(search).get(param);
  return v && variants.includes(v) ? v : variants[0];
}

interface PrototypeBarProps {
  /** URL search param 名，如 "efproto" */
  param: string;
  /** 变体键列表，如 ["a","b","c"] */
  variants: string[];
  /** 变体键 → 人类可读名 */
  labels: Record<string, string>;
  /** 附加控件（如能力模拟开关），渲染在变体切换区右侧 */
  extra?: ReactNode;
}

/**
 * 浮动底栏：左右箭头循环切换变体，URL 可分享、刷新稳定。
 * 键盘 ← / → 也可切换（输入框聚焦时不拦截）。仅 DEV 构建渲染。
 */
export function PrototypeBar({ param, variants, labels, extra }: PrototypeBarProps) {
  const [location, navigate] = useLocation();
  const search = useSearch();
  const current = usePrototypeVariant(param, variants);

  const cycle = (dir: 1 | -1) => {
    const idx = variants.indexOf(current);
    const next = variants[(idx + dir + variants.length) % variants.length];
    const params = new URLSearchParams(search);
    params.set(param, next);
    navigate(`${location}?${params.toString()}`, { replace: true });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (
        el instanceof HTMLElement &&
        (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)
      )
        return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!PROTOTYPE_ENABLED) return null;

  return (
    <div
      className="fixed bottom-4 left-1/2 z-[999] flex -translate-x-1/2 items-center gap-2 rounded-full px-3 py-1.5"
      style={{
        background: "oklch(0.12 0.02 300 / 0.92)",
        border: "1px solid oklch(0.65 0.18 300 / 0.5)",
        boxShadow: "0 8px 30px -8px oklch(0.65 0.18 300 / 0.6)",
        backdropFilter: "blur(8px)",
      }}
    >
      <FlaskConical className="h-3.5 w-3.5" style={{ color: "oklch(0.75 0.15 300)" }} />
      <span
        className="num text-[10px] uppercase"
        style={{ color: "oklch(0.75 0.15 300)", letterSpacing: "1px" }}
      >
        原型
      </span>
      <button
        type="button"
        onClick={() => cycle(-1)}
        aria-label="上一个变体"
        className="focus-ring grid h-6 w-6 place-items-center rounded-full transition-colors hover:bg-[oklch(1_0_0_/_0.1)]"
        style={{ color: "var(--color-text-2)" }}
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>
      <span
        className="min-w-[180px] text-center text-[12px] font-semibold"
        style={{ color: "var(--color-text)" }}
      >
        {current.toUpperCase()} — {labels[current]}
      </span>
      <button
        type="button"
        onClick={() => cycle(1)}
        aria-label="下一个变体"
        className="focus-ring grid h-6 w-6 place-items-center rounded-full transition-colors hover:bg-[oklch(1_0_0_/_0.1)]"
        style={{ color: "var(--color-text-2)" }}
      >
        <ChevronRight className="h-3.5 w-3.5" />
      </button>
      {extra && (
        <>
          <span
            aria-hidden
            className="mx-1 h-4 w-px"
            style={{ background: "oklch(0.65 0.18 300 / 0.4)" }}
          />
          {extra}
        </>
      )}
    </div>
  );
}
