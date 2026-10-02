// PROTOTYPE — #2979 的底部切换栏。故意做成白底黑字，与被评估的暗色界面区分开。
// ←/→ 切换大厅方案（输入框聚焦或有弹层打开时不拦截）。

import { useEffect } from "react";
import { useLocation } from "wouter";
import {
  LOBBY_VARIANTS,
  PROTO_ENABLED,
  WIZARD_VARIANTS,
  setProtoParam,
  useProtoParams,
  type LobbyVariant,
} from "./proto-params";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

const KEYS = Object.keys(LOBBY_VARIANTS) as LobbyVariant[];

export function ProtoBar() {
  const [location] = useLocation();
  if (!PROTO_ENABLED || !/^\/app\/projects\/?$/.test(location)) return null;
  return <Bar />;
}

function Seg<T extends string>({ value, options, onPick }: { value: T; options: Record<T, string>; onPick: (v: T) => void }) {
  return (
    <>
      {(Object.keys(options) as T[]).map((k) => (
        <button
          key={k}
          type="button"
          onClick={() => onPick(k)}
          title={options[k]}
          className={`rounded-full px-2.5 py-1 ${value === k ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"}`}
        >
          {options[k]}
        </button>
      ))}
    </>
  );
}

function Bar() {
  const p = useProtoParams();

  const cycle = (dir: 1 | -1) => {
    const i = KEYS.indexOf(p.variant);
    setProtoParam("variant", KEYS[(i + dir + KEYS.length) % KEYS.length]);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      if (document.querySelector("[role=dialog],[role=alertdialog],[role=menu]")) return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const sep = <span className="mx-1 h-4 w-px bg-neutral-200" />;

  return (
    <div
      data-proto-bar
      className="pointer-events-auto fixed bottom-4 left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900"
      style={{ colorScheme: "light" }}
    >
      <div className="flex w-max items-center gap-0.5 rounded-full bg-white px-1.5 py-1 shadow-[0_8px_30px_rgba(0,0,0,0.5)]">
        <span className="px-2 text-neutral-500">#2979 大厅</span>
        <button type="button" className="rounded-full px-2 py-1 hover:bg-neutral-100" onClick={() => cycle(-1)} aria-label="上一个方案">
          ←
        </button>
        <span className="min-w-[120px] px-1 text-center font-medium">
          {p.variant}（{LOBBY_VARIANTS[p.variant]}）
        </span>
        <button type="button" className="rounded-full px-2 py-1 hover:bg-neutral-100" onClick={() => cycle(1)} aria-label="下一个方案">
          →
        </button>
        {p.variant !== "A" && (
          <>
            {sep}
            <span className="pl-1 text-neutral-500">风格标签</span>
            <Seg value={p.tag} options={{ badge: "海报角标", none: "删除" }} onPick={(v) => setProtoParam("tag", v)} />
            {sep}
            <button
              type="button"
              onClick={() => setProtoParam("empty", p.empty ? null : "1")}
              className={`rounded-full px-2.5 py-1 ${p.empty ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"}`}
            >
              空状态
            </button>
          </>
        )}
        {sep}
        <span className="pl-1 text-neutral-500">向导</span>
        <Seg value={p.wizard} options={WIZARD_VARIANTS} onPick={(v) => setProtoParam("wizard", v)} />
        {p.wizard !== "A" && (
          <Seg value={p.wizardHeight} options={{ fixed: "固定高", auto: "随内容" }} onPick={(v) => setProtoParam("wh", v)} />
        )}
        <button
          type="button"
          onClick={() => setProtoParam("open", "1")}
          className="ml-1 rounded-full bg-violet-600 px-3 py-1 font-medium text-white hover:bg-violet-700"
        >
          打开向导
        </button>
      </div>
    </div>
  );
}
