// PROTOTYPE — #2971 切换条。白底黑字，与被评估的暗色界面区分开；抬高到保存栏上方，不遮住它。
// ←/→ 切换变体，G 开关辅助线（输入框聚焦时不拦截）。读数每 400ms 量一次。

import { useEffect, useState } from "react";
import { PS_VARIANT_NAMES, usePsParams } from "./ps-variant";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

function measure() {
  const w = (sel: string) => {
    const el = document.querySelector<HTMLElement>(sel);
    return el && el.offsetParent !== null ? Math.round(el.getBoundingClientRect().width) : null;
  };
  const owner = document.querySelector<HTMLElement>("[data-ps-scroll]");
  const doc = document.scrollingElement ?? document.documentElement;
  return {
    viewport: `${window.innerWidth}×${window.innerHeight}`,
    column: w('[data-ps-column]'),
    summary: w('[data-ps-summary]'),
    pages: owner ? (owner.scrollHeight / owner.clientHeight).toFixed(1) : "—",
    docScrolls: doc.scrollHeight > doc.clientHeight + 1,
  };
}

export function PsProtoBar() {
  const { variant, guides, cycle, toggleGuides } = usePsParams();
  const [r, setR] = useState(measure);

  useEffect(() => {
    const id = window.setInterval(() => setR(measure()), 400);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return;
      if (e.key === "ArrowLeft") cycle(-1);
      else if (e.key === "ArrowRight") cycle(1);
      else if (e.key === "g" || e.key === "G") toggleGuides();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cycle, toggleGuides]);

  if (import.meta.env.PROD) return null;

  return (
    <div
      className="fixed bottom-[68px] left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900"
      style={{ colorScheme: "light" }}
    >
      <div className="flex w-max items-center gap-1 rounded-full bg-white px-1.5 py-1 shadow-[0_8px_30px_rgba(0,0,0,0.5)]">
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cycle(-1)} aria-label="上一个变体">
          ←
        </button>
        <span className="px-1 font-medium">
          {variant}（{PS_VARIANT_NAMES[variant]}）
        </span>
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cycle(1)} aria-label="下一个变体">
          →
        </button>
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <button
          type="button"
          className={`rounded-full px-2.5 py-1 ${guides ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"}`}
          onClick={toggleGuides}
        >
          辅助线 G
        </button>
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <span className="whitespace-nowrap px-2 tabular-nums text-neutral-600">
          {r.viewport} · 内容列 {r.column ?? "—"} · 摘要栏 {r.summary ?? "无"} · 当前视图 {r.pages} 屏
          {r.docScrolls && <span className="ml-1 font-medium text-red-600">文档在滚动！</span>}
        </span>
      </div>
    </div>
  );
}
