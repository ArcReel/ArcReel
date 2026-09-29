// PROTOTYPE（#2752）：剪辑时间线只读预览的三个结构变体，挂在集页面上，`?variant=A|B|C` 切换。
// 数据是合成的样例剪辑时间线与素材（烧录了单元号与源素材时间码），不读写后端。
// `?tl=<时间线 ID>&t=<秒>` 模拟 Agent 在对话里给出的「跳到这里看」链接。
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "wouter";

import { resolveTimeline, TIMELINES } from "./model";
import { usePlayback } from "./usePlayback";
import { name as nameA, VariantA } from "./VariantA";
import { name as nameB, VariantB } from "./VariantB";
import { name as nameC, VariantC } from "./VariantC";

const VARIANTS = [
  { key: "A", name: nameA, C: VariantA },
  { key: "B", name: nameB, C: VariantB },
  { key: "C", name: nameC, C: VariantC },
] as const;

export function EditTimelinePreviewPrototype() {
  const [params, setParams] = useSearchParams();
  const variant = params.get("variant") ?? "A";
  const [timelineId, setTimelineId] = useState(() => params.get("tl") ?? TIMELINES[0].id);
  const r = useMemo(() => resolveTimeline(TIMELINES.find((t) => t.id === timelineId) ?? TIMELINES[0]), [timelineId]);
  const pb = usePlayback(r);

  // 首次进入按「跳到这里看」链接定位；之后换变体会重新挂载 <video>，按当前时间重新装载素材
  const initialT = params.get("t");
  const initialSeek = useRef(initialT ? Number(initialT) : 0);
  const mounted = useRef(false);
  useEffect(() => {
    pb.seek(mounted.current ? pb.t : initialSeek.current);
    mounted.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variant]);

  const V = (VARIANTS.find((v) => v.key === variant) ?? VARIANTS[0]).C;
  const cycle = (dir: 1 | -1) => {
    const i = VARIANTS.findIndex((v) => v.key === variant);
    const next = VARIANTS[(i + dir + VARIANTS.length) % VARIANTS.length].key;
    setParams(
      (p) => {
        p.set("variant", next);
        return p;
      },
      { replace: true },
    );
  };

  return (
    <div className="relative h-full">
      <V r={r} pb={pb} timelineId={r.timeline.id} onSelectTimeline={setTimelineId} variant={variant} />
      {import.meta.env.DEV && <PrototypeSwitcher variant={variant} onCycle={cycle} />}
    </div>
  );
}

function PrototypeSwitcher({ variant, onCycle }: { variant: string; onCycle: (d: 1 | -1) => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return;
      if (el instanceof HTMLElement && el.isContentEditable) return;
      if (e.key === "ArrowLeft") onCycle(-1);
      if (e.key === "ArrowRight") onCycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCycle]);
  const v = VARIANTS.find((x) => x.key === variant) ?? VARIANTS[0];
  return (
    <div className="fixed bottom-5 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-1 rounded-full bg-[#ffd400] px-1.5 py-1 text-[12.5px] font-semibold text-black shadow-[0_8px_30px_rgba(0,0,0,0.5)]">
      <button type="button" onClick={() => onCycle(-1)} className="rounded-full p-1 hover:bg-black/10" aria-label="上一个变体">
        <ChevronLeft className="h-4 w-4" />
      </button>
      <span className="px-2">
        PROTOTYPE {v.key}（{v.name}）
      </span>
      <button type="button" onClick={() => onCycle(1)} className="rounded-full p-1 hover:bg-black/10" aria-label="下一个变体">
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}
