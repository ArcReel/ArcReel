// PROTOTYPE（#2752）变体 A「剪辑台」：播放器在上，多轨横向时间线在下，点选片段看详情；修改通过对话交给 Agent。
import { AlertTriangle, Music } from "lucide-react";
import { useRef, useState } from "react";

import { fmt, TIMELINES, type ResolvedClip, unitHue } from "./model";
import { ISSUE_TONE, PlayButton, Stage } from "./shared";
import type { VariantProps } from "./types";

export const name = "剪辑台";

const LABEL_W = 56;

export function VariantA({ r, pb, timelineId, onSelectTimeline }: VariantProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const pct = (t: number) => `${(t / r.total) * 100}%`;
  const sel = r.clips.find((c) => c.clip.id === selected);

  const seekFromPointer = (e: React.PointerEvent) => {
    const box = trackRef.current?.getBoundingClientRect();
    if (!box) return;
    pb.seek(((e.clientX - box.left) / box.width) * r.total);
  };

  const ticks = Array.from({ length: Math.floor(r.total / 5) + 1 }, (_, i) => i * 5);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto px-6 py-5">
      {/* 剪辑时间线切换 */}
      <div className="flex flex-wrap items-center gap-3">
        <div role="tablist" className="flex rounded-[9px] border border-hairline bg-bg-grad-a/55 p-0.5">
          {TIMELINES.map((tl) => (
            <button
              key={tl.id}
              role="tab"
              aria-selected={tl.id === timelineId}
              type="button"
              onClick={() => onSelectTimeline(tl.id)}
              className={`rounded-[7px] px-3 py-1.5 text-[12.5px] transition-colors ${
                tl.id === timelineId ? "bg-accent-dim text-text" : "text-text-3 hover:text-text"
              }`}
            >
              {tl.name}
            </button>
          ))}
        </div>
        <span className="text-[12px] text-text-4">
          {r.timeline.updatedBy} 修改于 {r.timeline.updatedAt}
        </span>
      </div>

      {/* 播放器 */}
      <div className="mx-auto w-full max-w-[760px]">
        <Stage r={r} pb={pb} />
        <div className="mt-2.5 flex items-center gap-3">
          <PlayButton pb={pb} />
          <span className="text-[13px] text-text-2 tabular-nums">
            {fmt(pb.t)} <span className="text-text-4">/ {fmt(r.total)}</span>
          </span>
          <span className="text-[12px] text-text-4">
            {r.playable.length} 个片段 · 转场效果以成片为准
          </span>
        </div>
      </div>

      {/* 多轨 */}
      <div className="rounded-[10px] border border-hairline bg-bg-grad-b/60 p-3">
        <div className="relative" style={{ paddingLeft: LABEL_W }}>
          {/* 标尺 */}
          <div className="relative mb-1 h-4 text-[10px] text-text-4 tabular-nums">
            {ticks.map((t) => (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: pct(t) }}>
                {t}s
              </span>
            ))}
          </div>
          <div ref={trackRef} className="relative cursor-pointer" onPointerDown={seekFromPointer}>
            <TrackRow label="视频">
              {r.clips.map((c) =>
                c.missing ? (
                  <MissingMarker key={c.clip.id} c={c} left={pct(c.start)} onClick={() => setSelected(c.clip.id)} />
                ) : (
                  <ClipBlock
                    key={c.clip.id}
                    c={c}
                    left={pct(c.start)}
                    width={pct(c.duration)}
                    active={pb.current?.clip.id === c.clip.id}
                    selected={selected === c.clip.id}
                    onSelect={() => setSelected(c.clip.id)}
                  />
                ),
              )}
              {r.clips.map(
                (c) =>
                  c.transitionOut &&
                  !c.missing && (
                    <div
                      key={`tr-${c.clip.id}`}
                      title={`${c.transitionOut.kind} ${c.transitionOut.duration}s`}
                      className="pointer-events-none absolute top-1/2 z-10 flex h-5 -translate-y-1/2 items-center justify-center"
                      style={{
                        left: `calc(${pct(c.end)} - ${(c.transitionOut.duration / 2 / r.total) * 100}%)`,
                        width: pct(c.transitionOut.duration),
                      }}
                    >
                      <span className="h-full w-full rounded-[3px] bg-accent/35 ring-1 ring-accent" />
                    </div>
                  ),
              )}
            </TrackRow>
            <TrackRow label="旁白" slim>
              {r.narrations.map((n) => (
                <div
                  key={n.unitId}
                  className="absolute inset-y-1 overflow-hidden rounded-[4px] border border-warm/50 bg-warm-soft px-1.5 text-[10.5px] leading-[22px] text-warm"
                  style={{ left: pct(n.start), width: pct(n.end - n.start) }}
                  title={n.text}
                >
                  <span className="truncate">{n.text}</span>
                </div>
              ))}
            </TrackRow>
            <TrackRow label="字幕" slim>
              {r.subtitles.map((s, i) => (
                <div
                  key={i}
                  className="absolute inset-y-1.5 overflow-hidden whitespace-nowrap rounded-[3px] bg-surface-2 px-1 text-[10px] leading-[18px] text-text-3"
                  style={{ left: pct(s.start), width: `calc(${pct(s.end - s.start)} - 2px)` }}
                  title={s.text}
                >
                  {s.text}
                </div>
              ))}
            </TrackRow>
            <TrackRow label="BGM" slim>
              {r.timeline.bgm ? (
                <div
                  className="absolute inset-y-1 flex items-center gap-1 overflow-hidden rounded-[4px] border border-good/40 bg-good/10 px-1.5 text-[10.5px] text-good"
                  style={{ left: pct(r.timeline.bgm.start), width: pct(Math.min(r.timeline.bgm.end, r.total) - r.timeline.bgm.start) }}
                >
                  <Music className="h-3 w-3 shrink-0" />
                  <span className="truncate">
                    {r.timeline.bgm.name} · 音量 {Math.round(r.timeline.bgm.volume * 100)}% · 淡入 1s / 淡出 2s
                  </span>
                </div>
              ) : (
                <span className="absolute inset-y-0 left-1 text-[10.5px] leading-[30px] text-text-4">无</span>
              )}
            </TrackRow>
            {/* 播放头 */}
            <div
              className="pointer-events-none absolute -top-1 bottom-0 z-20 w-px bg-text"
              style={{ left: pct(pb.t) }}
            >
              <span className="absolute -left-[4px] -top-1 h-2 w-2 rotate-45 bg-text" />
            </div>
          </div>
        </div>

        {r.unusedUnits.length > 0 && (
          <div className="mt-3 flex items-center gap-2 border-t border-hairline-soft pt-2.5 text-[12px] text-text-3">
            <span>未使用</span>
            {r.unusedUnits.map((u) => (
              <span key={u.id} className="inline-flex items-center gap-1.5 rounded-[6px] border border-dashed border-hairline px-1.5 py-0.5">
                <img src={u.thumb} alt="" className="h-4 w-7 rounded-[2px] object-cover" />
                {u.id}
                <span className="text-text-4">{u.title}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 详情 + 提示 */}
      <div className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
        <div className="rounded-[10px] border border-hairline p-4" style={{ background: "var(--color-bg-grad-a)" }}>
          {sel ? (
            <ClipInspector c={sel} />
          ) : (
            <p className="text-[12.5px] text-text-3">选择视频轨上的片段查看详情</p>
          )}
        </div>
        <div className="rounded-[10px] border border-hairline p-4" style={{ background: "var(--color-bg-grad-a)" }}>
          <h3 className="mb-2 text-[13px] font-medium text-text">问题（{r.issues.length}）</h3>
          <ul className="space-y-1.5">
            {r.issues.map((iss, i) => (
              <li key={i}>
                <button
                  type="button"
                  className="flex w-full items-start gap-2 rounded-[6px] px-1.5 py-1 text-left text-[12px] text-text-2 hover:bg-bg-grad-b"
                  onClick={() => iss.clipId && setSelected(iss.clipId)}
                >
                  <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${ISSUE_TONE[iss.kind].dot}`} />
                  {iss.message}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function TrackRow({ label, slim, children }: { label: string; slim?: boolean; children: React.ReactNode }) {
  return (
    <div className={`relative ${slim ? "h-[30px]" : "h-[58px]"} border-b border-hairline-soft last:border-b-0`}>
      <span
        className="absolute top-1/2 -translate-y-1/2 text-[11px] text-text-3"
        style={{ left: -LABEL_W, width: LABEL_W - 8 }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function ClipBlock({
  c,
  left,
  width,
  active,
  selected,
  onSelect,
}: {
  c: ResolvedClip;
  left: string;
  width: string;
  active: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const hue = unitHue(c.unit.id);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`absolute inset-y-1.5 overflow-hidden rounded-[5px] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        selected ? "ring-2 ring-text" : active ? "ring-1 ring-accent" : ""
      } ${c.staleTrim ? "border border-dashed border-warn" : "border border-black/30"}`}
      style={{ left, width: `calc(${width} - 2px)`, background: `oklch(0.42 0.07 ${hue})` }}
      title={`${c.clip.id} ${c.unit.id}：${c.unit.title}${c.staleTrim ? "（素材已更新，暂用完整视频）" : ""}`}
    >
      <span className="relative flex h-full flex-col justify-between p-1 text-[10.5px] leading-none text-white">
        <span className="flex items-center gap-1">
          <b>{c.clip.id}</b>
          <span className="opacity-80">{c.unit.id}</span>
          {c.staleTrim && <AlertTriangle className="h-3 w-3 text-warn" />}
        </span>
        <span className="opacity-75 tabular-nums">{c.duration.toFixed(1)}s</span>
      </span>
    </button>
  );
}

function MissingMarker({ c, left, onClick }: { c: ResolvedClip; left: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={onClick}
      title={`${c.clip.id}：素材已删除，已跳过`}
      className="absolute inset-y-0 z-10 flex w-4 -translate-x-1/2 flex-col items-center"
      style={{ left }}
    >
      <span className="h-full w-[2px] bg-danger" />
      <span className="absolute -bottom-1 rounded-[3px] bg-danger px-1 text-[9.5px] leading-[14px] text-black">
        {c.clip.id}
      </span>
    </button>
  );
}

function ClipInspector({ c }: { c: ResolvedClip }) {
  return (
    <div className="flex gap-4">
      <img src={c.unit.thumb} alt="" className="h-[68px] w-[120px] shrink-0 rounded-[6px] object-cover" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-medium text-text">
            {c.clip.id} <span className="text-text-3">· {c.unit.id}</span>
          </h3>
          <span className="truncate text-[12px] text-text-3">{c.unit.title}</span>
        </div>
        <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12px] tabular-nums">
          <dt className="text-text-4">位置</dt>
          <dd className={c.missing ? "text-danger-2" : "text-text-2"}>
            {c.missing ? "素材已删除，已跳过" : `${fmt(c.start)} – ${fmt(c.end)}（${c.duration.toFixed(1)}s）`}
          </dd>
          <dt className="text-text-4">截取</dt>
          <dd className="text-text-2">
            <span className={c.staleTrim ? "text-text-4 line-through" : ""}>
              {c.clip.in.toFixed(1)}–{c.clip.out.toFixed(1)}s
            </span>
            （素材共 {c.unit.duration}s）
            {c.staleTrim && <span className="ml-1.5 text-warn">素材已更新，暂用完整视频</span>}
          </dd>
          {c.transitionOut && (
            <>
              <dt className="text-text-4">转场</dt>
              <dd className="text-text-2">
                {c.transitionOut.kind} {c.transitionOut.duration}s
              </dd>
            </>
          )}
          {c.clip.reason && (
            <>
              <dt className="text-text-4">说明</dt>
              <dd className="text-text-2">{c.clip.reason}</dd>
            </>
          )}
        </dl>
      </div>
    </div>
  );
}
