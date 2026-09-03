// PROTOTYPE — 变体切换浮条（wayfinder #2310）。生产构建返回 null，不会随包发布。
import { useEffect } from "react";
import { useLocation, useSearch } from "wouter";
import { ChevronLeft, ChevronRight } from "lucide-react";

export interface PrototypeVariant {
  key: string;
  name: string;
}

interface PrototypeSwitcherProps {
  variants: PrototypeVariant[];
  current: string;
}

export function PrototypeSwitcher({ variants, current }: PrototypeSwitcherProps) {
  const [location, navigate] = useLocation();
  const search = useSearch();

  const idx = Math.max(
    0,
    variants.findIndex((v) => v.key === current),
  );

  const go = (delta: number) => {
    const next = variants[(idx + delta + variants.length) % variants.length];
    const p = new URLSearchParams(search);
    p.set("variant", next.key);
    navigate(`${location}?${p.toString()}`, { replace: true });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (
        el instanceof HTMLElement &&
        (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)
      )
        return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!import.meta.env.DEV) return null;

  return (
    <div
      className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full border px-1.5 py-1 shadow-2xl shadow-black/60"
      style={{
        background: "oklch(0.98 0 0)",
        borderColor: "oklch(0.85 0 0)",
        color: "oklch(0.2 0 0)",
      }}
    >
      <button
        type="button"
        onClick={() => go(-1)}
        aria-label="上一个变体"
        className="rounded-full p-1.5 transition-colors hover:bg-black/10"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <span className="min-w-[200px] px-1 text-center font-mono text-[11px] font-bold tracking-wide">
        原型 {variants[idx].key} · {variants[idx].name}
      </span>
      <button
        type="button"
        onClick={() => go(1)}
        aria-label="下一个变体"
        className="rounded-full p-1.5 transition-colors hover:bg-black/10"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}
