/**
 * PROTOTYPE — throwaway（wayfinder #1481）。
 *
 * step1 按集预览面板三变体的入口 + 底部浮动切换条。
 * 进入方式：reference_video 剧集的「预处理」tab，URL 加 `?variant=A|B|C`。
 * 切换条另带数据态开关：干净待确认 / 违约隔离草稿。
 * 生产构建不渲染（import.meta.env.PROD 直接短路）。
 */
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { SCENARIO_CLEAN, SCENARIO_QUARANTINED } from "./mock";
import { VariantAudit } from "./VariantAudit";
import { VariantManuscript } from "./VariantManuscript";
import { VariantReport } from "./VariantReport";

const VARIANTS = [
  { key: "A", name: "文稿流", Component: VariantManuscript },
  { key: "B", name: "对照审计", Component: VariantAudit },
  { key: "C", name: "报告中枢", Component: VariantReport },
] as const;

type VariantKey = (typeof VARIANTS)[number]["key"];

export function currentPrototypeVariant(): string | null {
  if (import.meta.env.PROD) return null;
  return new URLSearchParams(window.location.search).get("variant");
}

function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

export function Step1PreviewPrototype() {
  const initial = (currentPrototypeVariant() ?? "A").toUpperCase();
  const [variant, setVariant] = useState<VariantKey>(
    VARIANTS.some((v) => v.key === initial) ? (initial as VariantKey) : "A",
  );
  const [quarantined, setQuarantined] = useState(false);

  const cycle = (dir: 1 | -1) => {
    setVariant((cur) => {
      const idx = VARIANTS.findIndex((v) => v.key === cur);
      const next = VARIANTS[(idx + dir + VARIANTS.length) % VARIANTS.length].key;
      const url = new URL(window.location.href);
      url.searchParams.set("variant", next);
      window.history.replaceState(null, "", url);
      return next;
    });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isEditableTarget(e.target)) return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const active = VARIANTS.find((v) => v.key === variant) ?? VARIANTS[0];
  const scenario = quarantined ? SCENARIO_QUARANTINED : SCENARIO_CLEAN;

  return (
    <div className="pb-16">
      <active.Component scenario={scenario} />

      <div
        className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full border border-hairline-strong px-2 py-1.5 shadow-lg backdrop-blur-md"
        style={{ background: "oklch(0.14 0.01 265 / 0.92)" }}
      >
        <button
          type="button"
          onClick={() => cycle(-1)}
          aria-label="上一个变体"
          className="rounded-full p-1 text-text-3 hover:bg-bg-grad-a hover:text-text"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="min-w-[7.5rem] text-center font-mono text-[11.5px] text-text">
          {active.key} — {active.name}
        </span>
        <button
          type="button"
          onClick={() => cycle(1)}
          aria-label="下一个变体"
          className="rounded-full p-1 text-text-3 hover:bg-bg-grad-a hover:text-text"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <span className="mx-1 h-4 w-px bg-hairline-strong" aria-hidden="true" />
        <button
          type="button"
          onClick={() => setQuarantined((q) => !q)}
          className={`rounded-full px-2.5 py-0.5 text-[11px] transition-colors ${
            quarantined ? "bg-red-500/20 text-red-300" : "bg-bg-grad-a text-text-3 hover:text-text"
          }`}
        >
          {quarantined ? "态：违约隔离草稿" : "态：干净待确认"}
        </button>
      </div>
    </div>
  );
}
