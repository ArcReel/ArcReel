// PROTOTYPE — #3091 顶部切换栏（底部留给保存栏）。白底黑字，与被评估的暗色界面区分开。
// ←/→ 切换变体（输入框聚焦时不拦截）；仅开发构建显示。

import { useEffect } from "react";
import { NEXT_VERSION } from "./seed";
import { VARIANT_NAME, syncVariantUrl, useProto } from "./store";

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return (el as HTMLElement).isContentEditable;
}

export function ProtoBar() {
  const variant = useProto((s) => s.variant);
  const cycle = useProto((s) => s.cycleVariant);
  const reset = useProto((s) => s.reset);
  const sync = useProto((s) => s.sync);
  const setOffline = useProto((s) => s.setOffline);
  const checkForUpdates = useProto((s) => s.checkForUpdates);

  useEffect(() => {
    syncVariantUrl();
    const id = window.setInterval(() => syncVariantUrl(), 400);
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.metaKey || e.ctrlKey || isTyping(document.activeElement)) return;
      if (e.key === "ArrowLeft") cycle(-1);
      if (e.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("keydown", onKey);
    };
  }, [cycle]);

  if (import.meta.env.PROD) return null;

  return (
    <div className="fixed top-2.5 left-1/2 z-[2147483000] -translate-x-1/2 font-sans text-[12px] text-neutral-900" style={{ colorScheme: "light" }}>
      <div className="flex w-max items-center gap-1 rounded-full bg-white px-1.5 py-1 shadow-[0_8px_30px_rgba(0,0,0,0.5)]">
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cycle(-1)} aria-label="上一个变体">
          ←
        </button>
        <span className="px-2 font-medium">
          {variant}（{VARIANT_NAME[variant]}）
        </span>
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={() => cycle(1)} aria-label="下一个变体">
          →
        </button>
        <span className="mx-1 h-4 w-px bg-neutral-200" />
        <span className="px-1 text-neutral-500 tabular-nums">官方数据 {sync.appliedVersion}</span>
        {variant === "B" ? (
          // B 的界面上没有同步状态与检查更新，这里替它模拟一次官方数据更新，便于看就地标出的官方值
          <button
            type="button"
            className="rounded-full px-2.5 py-1 hover:bg-neutral-100 disabled:text-neutral-400"
            disabled={sync.status === "checking" || sync.appliedVersion >= NEXT_VERSION}
            onClick={checkForUpdates}
          >
            {sync.status === "checking" ? "更新中……" : "模拟官方更新"}
          </button>
        ) : (
          <button
            type="button"
            className={`rounded-full px-2.5 py-1 ${sync.status === "offline" ? "bg-neutral-900 text-white" : "hover:bg-neutral-100"}`}
            onClick={() => setOffline(sync.status !== "offline")}
          >
            {sync.status === "offline" ? "已模拟离线" : "模拟离线"}
          </button>
        )}
        <button type="button" className="rounded-full px-2.5 py-1 hover:bg-neutral-100" onClick={reset}>
          重置数据
        </button>
      </div>
    </div>
  );
}
