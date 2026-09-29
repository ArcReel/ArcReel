// PROTOTYPE（#2752）：三个变体共用的播放画面与「交给 Agent」「跳到这里看」辅助。
import { Pause, Play } from "lucide-react";

import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";

import { fmt, type ResolvedClip, type ResolvedTimeline } from "./model";
import type { Playback } from "./usePlayback";

/** 把上下文预填进 Agent 输入框并打开面板；用户补一句要求后自己发送 */
export function askAgent(text: string) {
  useAssistantStore.getState().setInput(text);
  useAppStore.getState().setAssistantPanelOpen(true);
}

export function clipRef(r: ResolvedTimeline, c: ResolvedClip) {
  return `剪辑片段 ${c.clip.id}（视频单元 ${c.unit.id}，时间线 ${fmt(c.start)}–${fmt(c.end)}，源素材 ${c.effIn.toFixed(1)}–${c.effOut.toFixed(1)}s）`;
}

export function timelineRef(r: ResolvedTimeline) {
  return `剪辑时间线「${r.timeline.name}」r${r.timeline.revision}`;
}

/** 「跳到这里看」链接：Agent 在对话里给出的也是这种形态 */
export function deepLink(r: ResolvedTimeline, t: number, variant: string) {
  const u = new URL(window.location.href);
  u.search = "";
  u.searchParams.set("variant", variant);
  u.searchParams.set("tl", r.timeline.id);
  u.searchParams.set("t", t.toFixed(2));
  return u.toString();
}

export function Stage({
  r,
  pb,
  className = "",
  showMeta = true,
}: {
  r: ResolvedTimeline;
  pb: Playback;
  className?: string;
  showMeta?: boolean;
}) {
  const cue = r.subtitles.find((s) => pb.t >= s.start && pb.t < s.end);
  const c = pb.current;
  return (
    <div className={`relative aspect-video w-full overflow-hidden rounded-[10px] bg-black ${className}`}>
      {pb.videoRefs.map((ref, i) => {
        const active = pb.activeSlot === i;
        const fadingIn = pb.fade && pb.fade.slot === i;
        return (
          <video
            key={i}
            ref={ref}
            playsInline
            preload="auto"
            className="absolute inset-0 h-full w-full object-contain"
            style={{
              zIndex: active ? 2 : 1,
              opacity: active ? 1 : pb.fade ? 1 : 0,
              transition: active && fadingIn ? `opacity ${pb.fade!.duration}s linear` : "none",
            }}
          />
        );
      })}
      {cue && (
        <div className="pointer-events-none absolute inset-x-0 bottom-[9%] z-10 flex justify-center px-6">
          <span className="rounded-[4px] bg-black/55 px-2.5 py-1 text-center text-[15px] font-medium leading-snug text-white [text-shadow:0_1px_2px_black]">
            {cue.text}
          </span>
        </div>
      )}
      {showMeta && c && (
        <div className="pointer-events-none absolute left-2.5 top-2.5 z-10 flex flex-wrap gap-1.5 text-[11px]">
          <span className="rounded-[4px] bg-black/60 px-1.5 py-0.5 text-white/90 tabular-nums">
            {c.clip.id} · {c.unit.id} v{c.unit.currentVersion}
          </span>
          {c.staleTrim && (
            <span className="rounded-[4px] bg-warn/90 px-1.5 py-0.5 text-black">裁切已忽略，整段播放</span>
          )}
        </div>
      )}
      {pb.fade && (
        <div className="pointer-events-none absolute right-2.5 top-2.5 z-10 rounded-[4px] bg-accent/80 px-1.5 py-0.5 text-[11px] text-black">
          {pb.fade.label}
        </div>
      )}
      {!pb.playing && (
        <button
          type="button"
          onClick={pb.play}
          className="absolute inset-0 z-[5] flex items-center justify-center bg-black/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          aria-label="播放"
        >
          <span className="rounded-full bg-black/55 p-4 text-white">
            <Play className="h-6 w-6" />
          </span>
        </button>
      )}
    </div>
  );
}

export function PlayButton({ pb, size = "md" }: { pb: Playback; size?: "md" | "lg" }) {
  const cls = size === "lg" ? "h-10 w-10" : "h-8 w-8";
  return (
    <button
      type="button"
      onClick={pb.toggle}
      className={`${cls} inline-flex shrink-0 items-center justify-center rounded-full bg-text text-bg transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}
      aria-label={pb.playing ? "暂停" : "播放"}
    >
      {pb.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 translate-x-px" />}
    </button>
  );
}

export const ISSUE_TONE = {
  stale_trim: { dot: "bg-warn", text: "text-warn", label: "裁切已忽略" },
  missing_unit: { dot: "bg-danger", text: "text-danger-2", label: "单元已删除" },
  unused_unit: { dot: "bg-text-4", text: "text-text-3", label: "未使用" },
  narration_overlap: { dot: "bg-danger", text: "text-danger-2", label: "旁白重叠" },
  narration_overflow: { dot: "bg-danger", text: "text-danger-2", label: "旁白越界" },
} as const;
