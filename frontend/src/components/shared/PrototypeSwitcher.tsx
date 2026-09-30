// PROTOTYPE 专用：底部浮动的变体切换条（?variant=），生产构建不渲染。
import { useEffect } from "react";
import { useLocation, useSearch } from "wouter";

interface Props {
  variants: { key: string; name: string }[];
}

export function usePrototypeVariant(keys: string[]): string {
  const search = useSearch();
  const v = new URLSearchParams(search).get("variant");
  return v && keys.includes(v) ? v : keys[0];
}

export function PrototypeSwitcher({ variants }: Props) {
  const [location, setLocation] = useLocation();
  const search = useSearch();
  const current = usePrototypeVariant(variants.map((v) => v.key));
  const idx = variants.findIndex((v) => v.key === current);

  const go = (delta: number) => {
    const next = variants[(idx + delta + variants.length) % variants.length];
    const params = new URLSearchParams(search);
    params.set("variant", next.key);
    setLocation(`${location}?${params.toString()}`, { replace: true });
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

  if (import.meta.env.PROD) return null;
  const cur = variants[idx];
  return (
    <div
      className="fixed bottom-4 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-2 rounded-full px-2 py-1.5 text-[12px] font-medium"
      style={{ background: "oklch(0.95 0.12 95)", color: "oklch(0.2 0 0)", boxShadow: "0 8px 28px -6px oklch(0 0 0 / 0.6)" }}
    >
      <button type="button" className="rounded-full px-2 py-0.5 hover:bg-black/10" onClick={() => go(-1)}>←</button>
      <span className="min-w-[180px] text-center">PROTOTYPE · {cur.key}（{cur.name}）</span>
      <button type="button" className="rounded-full px-2 py-0.5 hover:bg-black/10" onClick={() => go(1)}>→</button>
    </div>
  );
}
