// ─── PROTOTYPE（用后即弃，勿并入生产）─────────────────────────────────────────
// 悬浮变体切换条：左右箭头 / ← → 键循环切换 ?variant=，仅 DEV 构建渲染。
import { useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useLocation, useSearch } from "wouter";

export interface PrototypeSwitcherProps {
  /** variant key → 展示名 */
  variants: Record<string, string>;
  current: string;
  /** 当前原型内部状态的只读摘要，随切换/操作实时可见 */
  stateLabel?: string;
}

export function useVariantParam(keys: readonly string[]): string | null {
  const search = useSearch();
  if (!import.meta.env.DEV) return null;
  const v = new URLSearchParams(search).get("variant");
  return v && keys.includes(v) ? v : null;
}

export function PrototypeSwitcher({ variants, current, stateLabel }: PrototypeSwitcherProps) {
  const [path, navigate] = useLocation();
  const keys = Object.keys(variants);

  const go = (dir: 1 | -1) => {
    const idx = keys.indexOf(current);
    const next = keys[(idx + dir + keys.length) % keys.length];
    navigate(`${path}?variant=${next}`, { replace: true });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!import.meta.env.DEV) return null;

  return (
    <div
      className="fixed bottom-4 left-1/2 z-[9999] flex -translate-x-1/2 items-center gap-1 rounded-full border border-warm-ring bg-[oklch(0.12_0.01_265_/_0.92)] py-1 pl-1 pr-3 shadow-[0_8px_30px_oklch(0_0_0_/_0.6)] backdrop-blur-md"
      role="toolbar"
      aria-label="Prototype variant switcher"
    >
      <button
        type="button"
        onClick={() => go(-1)}
        aria-label="Previous variant"
        className="grid h-7 w-7 place-items-center rounded-full text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => go(1)}
        aria-label="Next variant"
        className="grid h-7 w-7 place-items-center rounded-full text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
      <span className="ml-1 font-mono text-[10.5px] font-bold uppercase tracking-[0.1em] text-warm">
        PROTO {current} — {variants[current]}
      </span>
      {stateLabel ? (
        <span className="ml-2 border-l border-hairline pl-2 font-mono text-[10px] text-text-3">
          {stateLabel}
        </span>
      ) : null}
    </div>
  );
}
