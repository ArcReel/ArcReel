// PROTOTYPE（#2752）变体 C「放映室」：播放器占满主区，像审片一样边看边在时间点上记意见，
// 攒几条后一次交给 Agent。时间线结构只保留一条按比例分段的进度条和胶片条。
import { ChevronDown, Link2, MapPin, Send, X } from "lucide-react";
import { useEffect, useState } from "react";

import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";

import { clipAt, fmt, TIMELINES, unitHue } from "./model";
import { askAgent, deepLink, ISSUE_TONE, PlayButton, Stage, timelineRef } from "./shared";
import type { VariantProps } from "./types";

export const name = "放映室";

interface Note {
  t: number;
  clipId: string;
  unitId: string;
  src: number;
  text: string;
}

export function VariantC({ r, pb, timelineId, onSelectTimeline, variant }: VariantProps) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [draft, setDraft] = useState("");
  const [hover, setHover] = useState<number | null>(null);
  const [showIssues, setShowIssues] = useState(false);

  useEffect(() => setNotes([]), [timelineId]);

  const pct = (t: number) => `${(t / r.total) * 100}%`;
  const here = pb.current;

  const addNote = () => {
    if (!draft.trim() || !here) return;
    pb.pause();
    setNotes((ns) =>
      [...ns, { t: pb.t, clipId: here.clip.id, unitId: here.unit.id, src: here.effIn + (pb.t - here.start), text: draft.trim() }].sort(
        (a, b) => a.t - b.t,
      ),
    );
    setDraft("");
  };

  const sendNotes = () => {
    const lines = notes.map(
      (n) => `- ${fmt(n.t)}（${n.clipId} · ${n.unitId} 源素材 ${n.src.toFixed(1)}s）：${n.text}`,
    );
    askAgent(`看完${timelineRef(r)}，有这些意见：\n${lines.join("\n")}\n`);
  };

  const hoverClip = hover != null ? clipAt(r, hover) : undefined;

  return (
    <div className="flex h-full flex-col bg-bg-grad-b">
      {/* 顶栏：时间线切换 + 提示 */}
      <div className="flex items-center gap-3 px-5 py-3">
        <label className="relative inline-flex items-center">
          <span className="sr-only">剪辑时间线</span>
          <select
            value={timelineId}
            onChange={(e) => onSelectTimeline(e.target.value)}
            className="appearance-none rounded-[8px] border border-hairline bg-bg-grad-a/55 py-1.5 pl-3 pr-8 text-[13px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {TIMELINES.map((tl) => (
              <option key={tl.id} value={tl.id} className="bg-bg">
                {tl.name}（第 {tl.revision} 版）
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-text-3" />
        </label>
        {r.issues.length > 0 && (
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowIssues((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-full border border-warn/40 bg-warm-soft px-2.5 py-1 text-[12px] text-warn"
            >
              {r.issues.length} 条需要留意
            </button>
            {showIssues && (
              <ul className="absolute left-0 top-9 z-30 w-[360px] space-y-1 rounded-[10px] border border-hairline-strong bg-bg-grad-a p-2 shadow-xl">
                {r.issues.map((iss, i) => {
                  const c = r.clips.find((x) => x.clip.id === iss.clipId);
                  return (
                    <li key={i}>
                      <button
                        type="button"
                        className="flex w-full items-start gap-2 rounded-[6px] px-2 py-1.5 text-left text-[12px] text-text-2 hover:bg-bg-grad-b"
                        onClick={() => {
                          if (c && !c.missing) pb.seek(c.start);
                          setShowIssues(false);
                        }}
                      >
                        <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${ISSUE_TONE[iss.kind].dot}`} />
                        {iss.message}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
        <button
          type="button"
          className={`${GHOST_BTN_CLS} ml-auto`}
          onClick={() => void navigator.clipboard.writeText(deepLink(r, pb.t, variant))}
        >
          <Link2 className="h-3.5 w-3.5" /> 复制当前位置链接
        </button>
      </div>

      <div className="flex min-h-0 flex-1 gap-5 px-5 pb-16">
        {/* 主区：播放器 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-start justify-center">
            <div style={{ width: "min(100%, calc((100vh - 330px) * 16 / 9))" }}>
              <Stage r={r} pb={pb} />
            </div>
          </div>

          {/* 分段进度条 */}
          <div className="mt-4 flex items-center gap-3">
            <PlayButton pb={pb} size="lg" />
            <div className="min-w-0 flex-1">
              <div
                className="relative h-7 cursor-pointer"
                onPointerMove={(e) => {
                  const b = e.currentTarget.getBoundingClientRect();
                  setHover(((e.clientX - b.left) / b.width) * r.total);
                }}
                onPointerLeave={() => setHover(null)}
                onPointerDown={(e) => {
                  const b = e.currentTarget.getBoundingClientRect();
                  pb.seek(((e.clientX - b.left) / b.width) * r.total);
                }}
              >
                <div className="absolute inset-x-0 top-2 flex h-3 gap-px overflow-hidden rounded-[3px]">
                  {r.playable.map((c) => (
                    <span
                      key={c.clip.id}
                      className={c.staleTrim ? "bg-warn/70" : ""}
                      style={{
                        width: pct(c.duration),
                        background: c.staleTrim ? undefined : `oklch(0.55 0.06 ${unitHue(c.unit.id)})`,
                        opacity: pb.t >= c.end ? 1 : 0.55,
                      }}
                    />
                  ))}
                </div>
                {/* 旁白下划线 */}
                {r.narrations.map((n) => (
                  <span
                    key={n.unitId}
                    className="absolute top-[22px] h-[3px] rounded-full bg-warm/80"
                    style={{ left: pct(n.start), width: pct(n.end - n.start) }}
                  />
                ))}
                {/* 转场刻痕 */}
                {r.playable.map(
                  (c) =>
                    c.transitionOut && (
                      <span
                        key={`tr-${c.clip.id}`}
                        className="absolute top-0.5 h-5 w-[3px] -translate-x-1/2 rounded-full bg-accent-2"
                        style={{ left: pct(c.end) }}
                      />
                    ),
                )}
                {/* 意见标记 */}
                {notes.map((n, i) => (
                  <MapPin
                    key={i}
                    className="absolute -top-3 h-3.5 w-3.5 -translate-x-1/2 fill-accent text-bg"
                    style={{ left: pct(n.t) }}
                  />
                ))}
                <span className="absolute top-0 h-7 w-0.5 bg-text" style={{ left: pct(pb.t) }} />
                {hover != null && hoverClip && (
                  <div
                    className="pointer-events-none absolute bottom-9 z-20 -translate-x-1/2 rounded-[6px] border border-hairline-strong bg-bg-grad-a p-1 text-[11px] text-text-2 shadow-lg"
                    style={{ left: pct(hover) }}
                  >
                    <img src={hoverClip.unit.thumb} alt="" className="h-[54px] w-[96px] rounded-[4px] object-cover" />
                    <div className="mt-0.5 text-center tabular-nums">
                      {fmt(hover)} · {hoverClip.clip.id}
                    </div>
                  </div>
                )}
              </div>
            </div>
            <span className="text-[13px] text-text-2 tabular-nums">
              {fmt(pb.t)} <span className="text-text-4">/ {fmt(r.total)}</span>
            </span>
          </div>

          {/* 胶片条 */}
          <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1">
            {r.clips.map((c) => (
              <button
                key={c.clip.id}
                type="button"
                disabled={c.missing}
                onClick={() => pb.seek(c.start)}
                title={c.missing ? `${c.clip.id} 引用的 ${c.unit.id} 已删除` : `${c.clip.id} · ${c.unit.title}`}
                className={`relative shrink-0 overflow-hidden rounded-[5px] ${
                  here?.clip.id === c.clip.id ? "ring-2 ring-text" : "opacity-70 hover:opacity-100"
                } ${c.missing ? "w-6 bg-danger/30 ring-1 ring-danger" : "w-[88px]"}`}
              >
                {!c.missing && <img src={c.unit.thumb} alt="" className="h-[50px] w-full object-cover" />}
                <span className="absolute bottom-0.5 left-1 text-[10px] text-white [text-shadow:0_1px_2px_black]">
                  {c.clip.id}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* 意见栏 */}
        <aside className="flex w-[300px] shrink-0 flex-col rounded-[10px] border border-hairline bg-bg-grad-a/70">
          <div className="border-b border-hairline-soft px-4 py-3">
            <h3 className="text-[13.5px] font-medium text-text">看片意见</h3>
            <p className="mt-0.5 text-[12px] text-text-3">边看边记，记在当前时间点上，攒好后一次交给 Agent。</p>
          </div>
          <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
            {notes.length === 0 && <li className="px-2 py-3 text-[12px] text-text-4">还没有意见</li>}
            {notes.map((n, i) => (
              <li key={i} className="group flex gap-2 rounded-[6px] px-2 py-1.5 hover:bg-bg-grad-b">
                <button
                  type="button"
                  onClick={() => pb.seek(n.t)}
                  className="shrink-0 text-[11.5px] text-accent-2 tabular-nums hover:underline"
                >
                  {fmt(n.t)}
                </button>
                <span className="min-w-0 flex-1 text-[12.5px] text-text-2">
                  <span className="text-text-4">{n.clipId} </span>
                  {n.text}
                </span>
                <button
                  type="button"
                  aria-label="删除"
                  className="opacity-0 group-hover:opacity-100"
                  onClick={() => setNotes((ns) => ns.filter((_, j) => j !== i))}
                >
                  <X className="h-3.5 w-3.5 text-text-4" />
                </button>
              </li>
            ))}
          </ul>
          <div className="space-y-2 border-t border-hairline-soft p-3">
            <div className="text-[11.5px] text-text-4 tabular-nums">
              {here ? `在 ${fmt(pb.t)}（${here.clip.id} · ${here.unit.id}）` : "—"}
            </div>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onFocus={pb.pause}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  addNote();
                }
              }}
              rows={2}
              placeholder="这里节奏太拖、换个转场……"
              className={`${INPUT_CLS} resize-none`}
            />
            <div className="flex gap-2">
              <button type="button" className={GHOST_BTN_CLS} onClick={addNote} disabled={!draft.trim()}>
                记下
              </button>
              <button
                type="button"
                className={`${ACCENT_BTN_SM_CLS} ml-auto`}
                style={ACCENT_BUTTON_STYLE}
                disabled={notes.length === 0}
                onClick={sendNotes}
              >
                <Send className="h-3.5 w-3.5" /> 交给 Agent（{notes.length}）
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
