// PROTOTYPE — 工作区外壳原型（#2970）的布局小件：拖拽手柄、视口宽度、右缘竖条入口。

import { useEffect, useRef, useState } from "react";
import { Bot } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { AttentionDot, useAgentAttention } from "./HeaderPieces";
import { AGENT_RAIL_WIDTH } from "./axes";

export function useViewportWidth() {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return w;
}

export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setW(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/**
 * 竖向拖拽手柄：指针拖动、方向键每次 16px、双击复位。
 * `edge` 表示手柄贴在面板的哪一侧：left 手柄向左拖是变宽（Agent 面板），right 相反（侧栏）。
 */
export function DragHandle({
  edge,
  value,
  min,
  max,
  onChange,
  onReset,
  onDragging,
  label,
}: {
  edge: "left" | "right";
  value: number;
  min: number;
  max: number;
  onChange: (next: number) => void;
  onReset: () => void;
  onDragging: (dragging: boolean) => void;
  label: string;
}) {
  const start = useRef<{ x: number; w: number } | null>(null);
  const sign = edge === "left" ? -1 : 1;
  const clamp = (n: number) => Math.max(min, Math.min(max, Math.round(n)));
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      className={`group absolute inset-y-0 z-10 w-2 cursor-col-resize touch-none focus-visible:outline-none ${
        edge === "left" ? "-left-1" : "-right-1"
      }`}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, w: value };
        onDragging(true);
      }}
      onPointerMove={(e) => {
        if (!start.current) return;
        onChange(clamp(start.current.w + sign * (e.clientX - start.current.x)));
      }}
      onPointerUp={() => {
        start.current = null;
        onDragging(false);
      }}
      onPointerCancel={() => {
        start.current = null;
        onDragging(false);
      }}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") onChange(clamp(value - sign * 16));
        else if (e.key === "ArrowRight") onChange(clamp(value + sign * 16));
        else return;
        e.preventDefault();
      }}
      title={`${label}：拖动调宽，双击复位（${min}–${max}px）`}
    >
      <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary group-active:bg-primary" />
    </div>
  );
}

/** 收起后的右缘竖条：在文档流里占 40px，画布不会被盖住。 */
export function AgentRail() {
  const toggle = useAppStore((s) => s.toggleAssistantPanel);
  const attention = useAgentAttention();
  return (
    <button
      type="button"
      onClick={toggle}
      className="flex shrink-0 flex-col items-center gap-2 border-l border-border pt-3 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-ring"
      style={{ width: AGENT_RAIL_WIDTH }}
      title="打开 Agent 面板"
      aria-label="打开 Agent 面板"
    >
      <Bot className="size-4" />
      <span className="text-[12px] [writing-mode:vertical-rl]">Agent</span>
      <AttentionDot kind={attention} />
    </button>
  );
}
