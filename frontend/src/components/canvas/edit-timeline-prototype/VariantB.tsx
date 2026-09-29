// PROTOTYPE（#2752）变体 B「审阅单」：剪辑时间线读成一张自上而下的剪辑清单，播放器贴在右侧。
// 每行是一个剪辑片段，带剪辑理由与问题；转场是行间分隔；可多选几段一起交给 Agent。
import { ChevronDown, Link2, MessageSquarePlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

import { fmt, TIMELINES, type ResolvedClip, unitHue } from "./model";
import { askAgent, clipRef, deepLink, ISSUE_TONE, PlayButton, Stage, timelineRef } from "./shared";
import type { VariantProps } from "./types";

export const name = "审阅单";

export function VariantB({ r, pb, timelineId, onSelectTimeline, variant }: VariantProps) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const rowRefs = useRef<Map<string, HTMLLIElement>>(new Map());
  const currentId = pb.current?.clip.id;

  useEffect(() => setPicked(new Set()), [timelineId]);
  useEffect(() => {
    if (!pb.playing || !currentId) return;
    rowRefs.current.get(currentId)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [currentId, pb.playing]);

  const toggle = (id: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const sendPicked = () => {
    const lines = r.clips.filter((c) => picked.has(c.clip.id)).map((c) => `- ${clipRef(r, c)}`);
    askAgent(`关于${timelineRef(r)}的这几段：\n${lines.join("\n")}\n\n`);
  };

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(360px,42%)]">
      {/* 左：剪辑清单 */}
      <div className="relative flex min-h-0 flex-col border-r border-hairline-soft">
        <header className="border-b border-hairline-soft px-5 py-4">
          <label className="relative inline-flex items-center">
            <span className="sr-only">剪辑时间线</span>
            <select
              value={timelineId}
              onChange={(e) => onSelectTimeline(e.target.value)}
              className="appearance-none bg-transparent pr-6 text-[17px] font-medium text-text focus-visible:outline-none"
            >
              {TIMELINES.map((tl) => (
                <option key={tl.id} value={tl.id} className="bg-bg text-[13px]">
                  {tl.name}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-0 h-4 w-4 text-text-3" />
          </label>
          <p className="mt-1 text-[12px] text-text-3">
            第 {r.timeline.revision} 版 · {r.timeline.updatedBy}，{r.timeline.updatedAt} ·{" "}
            <span className="text-text-2">{r.timeline.lastSummary}</span>
          </p>
          <p className="mt-0.5 text-[12px] text-text-4 tabular-nums">
            {r.playable.length} 段，共 {fmt(r.total)}
            {r.issues.length > 0 && ` · ${r.issues.length} 条需要留意`}
          </p>
        </header>

        <ol className="min-h-0 flex-1 overflow-y-auto px-3 py-2 pb-20">
          {r.clips.map((c, i) => (
            <li key={c.clip.id} ref={(el) => void (el ? rowRefs.current.set(c.clip.id, el) : rowRefs.current.delete(c.clip.id))}>
              <ClipRow
                c={c}
                n={i + 1}
                current={currentId === c.clip.id}
                picked={picked.has(c.clip.id)}
                onToggle={() => toggle(c.clip.id)}
                onSeek={() => !c.missing && pb.seek(c.start)}
                narration={r.narrations.find((x) => x.clipId === c.clip.id)?.text}
                issues={r.issues.filter((x) => x.clipId === c.clip.id)}
              />
              {c.transitionOut && !c.missing && (
                <div className="my-0.5 flex items-center gap-2 pl-[88px] text-[11px] text-accent-2">
                  <span className="h-px w-5 bg-accent/50" />
                  {c.transitionOut.kind} {c.transitionOut.duration}s
                  <span className="h-px flex-1 bg-accent/20" />
                </div>
              )}
            </li>
          ))}
          {r.unusedUnits.length > 0 && (
            <li className="mt-4 border-t border-dashed border-hairline pt-3">
              <h4 className="px-2 text-[12px] text-text-3">没有进入这条剪辑时间线</h4>
              {r.unusedUnits.map((u) => (
                <div key={u.id} className="flex items-center gap-3 px-2 py-1.5 text-[12.5px] text-text-3">
                  <img src={u.thumb} alt="" className="h-8 w-14 rounded-[4px] object-cover opacity-60" />
                  <span className="text-text-2">{u.id}</span>
                  {u.title}
                  <button
                    type="button"
                    className="ml-auto text-[12px] text-accent-2 hover:underline"
                    onClick={() => askAgent(`${timelineRef(r)}没有用到视频单元 ${u.id}（${u.title}），`)}
                  >
                    问 Agent
                  </button>
                </div>
              ))}
            </li>
          )}
        </ol>

        {picked.size > 0 && (
          <div className="absolute inset-x-4 bottom-4 flex items-center gap-3 rounded-[10px] border border-hairline-strong bg-bg-grad-a/95 px-4 py-2.5 shadow-lg backdrop-blur">
            <span className="text-[12.5px] text-text-2">已选 {picked.size} 段</span>
            <button type="button" className="text-[12px] text-text-4 hover:text-text" onClick={() => setPicked(new Set())}>
              清除
            </button>
            <button type="button" className={`${ACCENT_BTN_SM_CLS} ml-auto`} style={ACCENT_BUTTON_STYLE} onClick={sendPicked}>
              <MessageSquarePlus className="h-3.5 w-3.5" /> 交给 Agent
            </button>
          </div>
        )}
      </div>

      {/* 右：播放器 */}
      <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto p-5">
        <Stage r={r} pb={pb} />
        <div className="flex items-center gap-3">
          <PlayButton pb={pb} />
          <span className="text-[13px] text-text-2 tabular-nums">
            {fmt(pb.t)} <span className="text-text-4">/ {fmt(r.total)}</span>
          </span>
          <button
            type="button"
            className={`${GHOST_BTN_CLS} ml-auto`}
            onClick={() => void navigator.clipboard.writeText(deepLink(r, pb.t, variant))}
          >
            <Link2 className="h-3.5 w-3.5" /> 复制位置
          </button>
        </div>
        {/* 片段分布条 */}
        <div
          className="relative flex h-3 cursor-pointer overflow-hidden rounded-full bg-bg-grad-b"
          onPointerDown={(e) => {
            const b = e.currentTarget.getBoundingClientRect();
            pb.seek(((e.clientX - b.left) / b.width) * r.total);
          }}
        >
          {r.playable.map((c) => (
            <span
              key={c.clip.id}
              className="h-full border-r border-bg"
              style={{ width: `${(c.duration / r.total) * 100}%`, background: `oklch(0.5 0.07 ${unitHue(c.unit.id)})` }}
            />
          ))}
          <span className="absolute inset-y-0 w-0.5 bg-text" style={{ left: `${(pb.t / r.total) * 100}%` }} />
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
          <dt className="text-text-4">旁白</dt>
          <dd className="text-text-2">{r.narrations.length} 段，跟随所在视频单元的第一个剪辑片段</dd>
          <dt className="text-text-4">字幕</dt>
          <dd className="text-text-2">跟随旁白</dd>
          <dt className="text-text-4">BGM</dt>
          <dd className="text-text-2">
            {r.timeline.bgm ? `${r.timeline.bgm.name}，音量 ${Math.round(r.timeline.bgm.volume * 100)}%，旁白处自动压低` : "无"}
          </dd>
        </dl>
      </aside>
    </div>
  );
}

function ClipRow({
  c,
  n,
  current,
  picked,
  onToggle,
  onSeek,
  narration,
  issues,
}: {
  c: ResolvedClip;
  n: number;
  current: boolean;
  picked: boolean;
  onToggle: () => void;
  onSeek: () => void;
  narration?: string;
  issues: { kind: keyof typeof ISSUE_TONE; message: string }[];
}) {
  const inPct = (c.clip.in / c.unit.duration) * 100;
  const outPct = (c.clip.out / c.unit.duration) * 100;
  return (
    <div
      className={`group flex gap-3 rounded-[8px] px-2 py-2 transition-colors ${
        current ? "bg-accent-dim" : picked ? "bg-bg-grad-a" : "hover:bg-bg-grad-a/60"
      } ${c.missing ? "opacity-70" : ""}`}
    >
      <div className="flex w-[22px] shrink-0 flex-col items-center gap-1.5 pt-0.5">
        <input
          type="checkbox"
          checked={picked}
          onChange={onToggle}
          aria-label={`选择 ${c.clip.id}`}
          className="h-3.5 w-3.5 accent-[var(--color-accent)]"
        />
        <span className="text-[11px] text-text-4 tabular-nums">{n}</span>
      </div>
      <button type="button" onClick={onSeek} disabled={c.missing} className="relative shrink-0 self-start">
        <img
          src={c.unit.thumb}
          alt=""
          className={`h-[45px] w-[80px] rounded-[5px] object-cover ${c.missing ? "grayscale" : ""}`}
        />
        {c.missing && <span className="absolute inset-0 rounded-[5px] ring-2 ring-inset ring-danger" />}
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[13px] font-medium text-text">{c.clip.id}</span>
          <span className="text-[12px] text-text-3">{c.unit.id}</span>
          <span className="truncate text-[12.5px] text-text-2">{c.unit.title}</span>
          <span className="ml-auto shrink-0 text-[11.5px] text-text-3 tabular-nums">
            {c.missing ? "跳过" : `${fmt(c.start)} · ${c.duration.toFixed(1)}s`}
          </span>
        </div>
        {/* 源素材里取了哪一段 */}
        {!c.missing && (
          <div className="mt-1 flex items-center gap-2">
            <div className="relative h-1 w-[120px] rounded-full bg-surface-2">
              <span
                className={`absolute inset-y-0 rounded-full ${c.staleTrim ? "bg-warn/40" : "bg-text-3"}`}
                style={{ left: `${inPct}%`, width: `${outPct - inPct}%` }}
              />
            </div>
            <span className="text-[11px] text-text-4 tabular-nums">
              源 {c.clip.in.toFixed(1)}–{c.clip.out.toFixed(1)}s / {c.unit.duration}s · v{c.unit.currentVersion}
            </span>
          </div>
        )}
        {c.clip.reason && <p className="mt-1 text-[12.5px] leading-[1.5] text-text-2">{c.clip.reason}</p>}
        {narration && (
          <p className="mt-1 text-[12px] leading-[1.5] text-warm">
            旁白：{narration}
          </p>
        )}
        {issues.map((iss, i) => (
          <p key={i} className={`mt-1 flex items-center gap-1.5 text-[12px] ${ISSUE_TONE[iss.kind].text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${ISSUE_TONE[iss.kind].dot}`} />
            {iss.message}
          </p>
        ))}
      </div>
    </div>
  );
}
