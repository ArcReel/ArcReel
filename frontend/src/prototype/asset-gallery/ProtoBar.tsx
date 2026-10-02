// PROTOTYPE — #2973 的底部切换栏。故意做成白底黑字，与被评估的暗色界面区分开。
// ←/→ 切换方案（输入框聚焦或有弹层打开时不拦截）。

import { useEffect } from "react";
import { useLocation } from "wouter";
import {
  GALLERY_VARIANTS,
  LIBRARY_VARIANTS,
  PROTO_ENABLED,
  setProtoParam,
  useProtoParams,
} from "./proto-params";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

const GALLERY_RE = /\/(characters|scenes|props|products)\/?$/;
const LIBRARY_RE = /\/app\/assets\/?$/;

export function ProtoBar() {
  const [location] = useLocation();
  const path = typeof window === "undefined" ? location : window.location.pathname;
  const kind = GALLERY_RE.test(path) ? "gallery" : LIBRARY_RE.test(path) ? "library" : null;
  if (!PROTO_ENABLED || !kind) return null;
  return <Bar kind={kind} />;
}

function Bar({ kind }: { kind: "gallery" | "library" }) {
  const { variant, actions } = useProtoParams();
  const variants: Record<string, string> = kind === "gallery" ? GALLERY_VARIANTS : LIBRARY_VARIANTS;
  const keys = Object.keys(variants);
  const current = keys.includes(variant) ? variant : "A";

  const cycle = (dir: 1 | -1) => {
    const i = keys.indexOf(current);
    setProtoParam("variant", keys[(i + dir + keys.length) % keys.length]);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      // 查看器、Sheet 等弹层打开时 ←/→ 归弹层使用
      if (document.querySelector("[data-proto-captures-arrows]")) return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div
      className="fixed bottom-4 left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900"
      style={{ colorScheme: "light" }}
    >
      <div className="flex w-max items-center gap-1 rounded-full bg-white px-1.5 py-1 shadow-[0_8px_30px_rgba(0,0,0,0.5)]">
        <span className="px-2 text-neutral-500">{kind === "gallery" ? "#2973 画廊" : "#2973 资产库"}</span>
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cycle(-1)} aria-label="上一个方案">
          ←
        </button>
        <span className="min-w-[180px] px-2 text-center font-medium">
          {current}（{variants[current]}）
        </span>
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cycle(1)} aria-label="下一个方案">
          →
        </button>
        {kind === "gallery" && current !== "A" && (
          <>
            <span className="mx-1 h-4 w-px bg-neutral-200" />
            <span className="pl-1 text-neutral-500">次要操作</span>
            {(["more", "pinned"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setProtoParam("actions", m)}
                className={`rounded-full px-2.5 py-1 ${actions === m ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"}`}
              >
                {m === "more" ? "全收进「更多」" : "常驻 2 个"}
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
