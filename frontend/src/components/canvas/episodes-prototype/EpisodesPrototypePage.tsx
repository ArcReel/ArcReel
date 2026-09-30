// PROTOTYPE（#2831，基于 #2767，一次性代码，勿合入 main）
// 「分集」视图原型，挂在 /app/projects/:name/episodes-prototype?variant=A|B|C。
// 本轮问题：整本源文由多个文件组成时，文件边界、文件顺序、源文件类型与增删替换调序怎么呈现和操作。
// 数据全是内存假数据；左下「账本状态 / 模拟」可看账本，并模拟文件在 ArcReel 之外被改动。
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { StateInspector } from "./shared";
import { useProto } from "./useProto";
import { View, type VariantKey } from "./View";

const VARIANTS: { key: VariantKey; name: string }[] = [
  { key: "A", name: "原文内文件条" },
  { key: "B", name: "右栏文件清单" },
  { key: "C", name: "分文件标签 + 管理模式" },
];

function readVariant(): VariantKey {
  const v = new URLSearchParams(window.location.search).get("variant");
  return (VARIANTS.find((x) => x.key === v)?.key ?? "A");
}

export function EpisodesPrototypePage() {
  const p = useProto();
  const [variant, setVariant] = useState<VariantKey>(readVariant);

  const go = (dir: 1 | -1) => {
    const i = VARIANTS.findIndex((x) => x.key === variant);
    const next = VARIANTS[(i + dir + VARIANTS.length) % VARIANTS.length].key;
    const url = new URL(window.location.href);
    url.searchParams.set("variant", next);
    window.history.replaceState(null, "", url);
    setVariant(next);
  };

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (!e.altKey) return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const cur = VARIANTS.find((x) => x.key === variant)!;
  return (
    <div className="relative h-full">
      <View key={variant} p={p} variant={variant} />
      <StateInspector p={p} />
      {import.meta.env.DEV && (
        <div className="fixed bottom-4 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-2 rounded-full px-2 py-1 text-[12px] shadow-lg" style={{ background: "oklch(0.95 0.12 95)", color: "oklch(0.2 0 0)" }}>
          <button type="button" aria-label="上一个变体" onClick={() => go(-1)}><ChevronLeft className="h-4 w-4" /></button>
          <span className="font-medium">{cur.key} · {cur.name}</span>
          <button type="button" aria-label="下一个变体" onClick={() => go(1)}><ChevronRight className="h-4 w-4" /></button>
          <span className="text-[10.5px] opacity-60">Alt + ← →</span>
        </div>
      )}
    </div>
  );
}
