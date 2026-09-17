// PROTOTYPE — 一次性代码，不进 main。
// 悬浮在页面底部中央的变体切换条：左右箭头循环切换 `?variant=`，←/→ 键同样有效，
// 输入控件聚焦时不劫持方向键。仅在开发构建渲染。
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { useLocation, useSearch } from "wouter";

interface PrototypeSwitcherProps {
  variants: { key: string; name: string }[];
  current: string;
  /** 切换条右侧的附加控件（例如「端点详情预览」）。 */
  extra?: ReactNode;
}

export function PrototypeSwitcher({ variants, current, extra }: PrototypeSwitcherProps) {
  const [location, navigate] = useLocation();
  const search = useSearch();

  const idx = Math.max(0, variants.findIndex((v) => v.key === current));
  const go = (delta: number) => {
    const next = variants[(idx + delta + variants.length) % variants.length];
    const params = new URLSearchParams(search);
    params.set("variant", next.key);
    params.delete("item");
    navigate(`${location}?${params.toString()}`, { replace: true });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!import.meta.env.DEV) return null;
  const cur = variants[idx];
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex justify-center">
      <div
        className="pointer-events-auto flex items-center gap-1 rounded-full border border-yellow-300/60 bg-yellow-300 px-1.5 py-1 text-[12px] font-semibold text-black shadow-[0_8px_30px_-8px_rgba(0,0,0,0.8)]"
        role="toolbar"
        aria-label="原型变体切换"
      >
        <button type="button" onClick={() => go(-1)} className="rounded-full p-1 hover:bg-black/10" aria-label="上一个变体">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="px-2 font-mono">
          PROTOTYPE · {cur.key.toUpperCase()} <span className="font-sans font-medium">{cur.name}</span>
          <span className="ml-2 text-black/50">{idx + 1}/{variants.length}</span>
        </span>
        <button type="button" onClick={() => go(1)} className="rounded-full p-1 hover:bg-black/10" aria-label="下一个变体">
          <ChevronRight className="h-4 w-4" />
        </button>
        {extra && <span className="ml-1 border-l border-black/20 pl-2">{extra}</span>}
      </div>
    </div>
  );
}
