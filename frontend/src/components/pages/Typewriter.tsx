import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

export interface TypewriterSegment {
  text: string;
  className?: string;
  style?: CSSProperties;
  after?: ReactNode;
}

/** caret 闪烁 1 次的周期（亮 + 灭），单位 ms。决定 linger 阶段总时长 = blinkPeriodMs * caretBlinkCount */
const CARET_BLINK_PERIOD_MS = 1400;
/** 吐字完成后 caret 还闪几次再消失 */
const CARET_BLINK_AFTER_DONE = 3;

interface TypewriterProps {
  segments: TypewriterSegment[];
  speed?: number;
  punctuationDelay?: number;
  startDelay?: number;
  segmentGap?: number;
  caret?: boolean;
  /** 同一 once key 同会话内只播放一次；后续 mount 直接显示完成态 */
  once?: string;
  className?: string;
  style?: CSSProperties;
  onDone?: () => void;
}

const PUNCTUATION = /[，。！？；：、,.!?;:…—]/;
const playedOnce = new Set<string>();

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * 打字机吐字效果，按 segments 串行播放；首段尾部插入 `after`（如 <br />）后再起第二段。
 * 标点和段落边界自动延长停顿，节奏贴近电影编辑机。
 */
export function Typewriter({
  segments,
  speed = 72,
  punctuationDelay = 220,
  startDelay = 140,
  segmentGap = 320,
  caret = true,
  once,
  className,
  style,
  onDone,
}: TypewriterProps) {
  const totalLen = useMemo(() => segments.reduce((n, s) => n + s.text.length, 0), [segments]);
  const fullText = useMemo(() => segments.map((s) => s.text).join(""), [segments]);

  const skip =
    totalLen === 0 || prefersReducedMotion() || (once ? playedOnce.has(once) : false);

  const [pos, setPos] = useState(skip ? totalLen : 0);
  const [phase, setPhase] = useState<"typing" | "linger" | "done">(skip ? "done" : "typing");
  // 文案 / skip 变化时立即重置进度，避免新文案被截断或卡在 done
  // (React 19 推荐的 render-time setState 模式：snapshot prev，发现 diff 时同步 setState 触发重渲)
  const [prevSnapshot, setPrevSnapshot] = useState({ fullText, skip });
  if (prevSnapshot.fullText !== fullText || prevSnapshot.skip !== skip) {
    setPrevSnapshot({ fullText, skip });
    setPos(skip ? totalLen : 0);
    setPhase(skip ? "done" : "typing");
  }
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  // segments 抓在 ref 里：调用方常常内联构造 segments，每次重渲都生成新引用；
  // 若把 segments 写进 effect 依赖，父组件无关重渲就会清旧定时器、重新 tick(0)，
  // 后续 setPos(next) 会把 pos 倒退回首字。fullText 才是真正的内容键。
  const segmentsRef = useRef(segments);
  useEffect(() => {
    segmentsRef.current = segments;
  });

  useEffect(() => {
    if (skip) {
      onDoneRef.current?.();
      return;
    }
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];

    const tick = (current: number) => {
      if (cancelled) return;
      if (current >= totalLen) {
        if (once) playedOnce.add(once);
        setPhase("linger");
        timers.push(
          setTimeout(() => {
            if (cancelled) return;
            setPhase("done");
            onDoneRef.current?.();
          }, CARET_BLINK_PERIOD_MS * CARET_BLINK_AFTER_DONE),
        );
        return;
      }
      let acc = 0;
      let ch = "";
      let crossesSegmentEnd = false;
      for (const seg of segmentsRef.current) {
        if (current < acc + seg.text.length) {
          ch = seg.text[current - acc];
          crossesSegmentEnd = current + 1 === acc + seg.text.length;
          break;
        }
        acc += seg.text.length;
      }
      const next = current + 1;
      const extra =
        crossesSegmentEnd && next < totalLen
          ? segmentGap
          : PUNCTUATION.test(ch)
            ? punctuationDelay
            : 0;
      timers.push(
        setTimeout(() => {
          if (cancelled) return;
          setPos(next);
          tick(next);
        }, speed + extra),
      );
    };

    timers.push(setTimeout(() => tick(0), startDelay));
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, [skip, totalLen, fullText, speed, punctuationDelay, segmentGap, startDelay, once]);

  const visibleNodes: ReactNode[] = [];
  let consumed = 0;
  segments.forEach((seg, i) => {
    const segEnd = consumed + seg.text.length;
    const visibleEnd = Math.max(consumed, Math.min(pos, segEnd));
    visibleNodes.push(
      <span key={`tw-s-${i}`} className={seg.className} style={seg.style}>
        {seg.text.slice(0, visibleEnd - consumed)}
      </span>,
    );
    if (seg.after && pos >= segEnd) {
      visibleNodes.push(<span key={`tw-a-${i}`}>{seg.after}</span>);
    }
    consumed = segEnd;
  });

  return (
    <span className={className} style={style}>
      <span className="sr-only">{fullText}</span>
      <span aria-hidden="true">
        {visibleNodes}
        {caret && phase !== "done" ? (
          <TypewriterCaret key={phase} finite={phase === "linger"} />
        ) : null}
      </span>
    </span>
  );
}

function TypewriterCaret({ finite }: { finite: boolean }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: "inline-block",
        width: 4,
        marginLeft: "0.2ch",
        height: "1em",
        verticalAlign: "-0.12em",
        background: "var(--color-accent-2)",
        boxShadow: "0 0 10px var(--color-accent-glow)",
        borderRadius: 1.5,
        animation: finite
          ? `tw-blink ${CARET_BLINK_PERIOD_MS}ms steps(2, end) ${CARET_BLINK_AFTER_DONE} forwards`
          : `tw-blink ${CARET_BLINK_PERIOD_MS}ms steps(2, end) infinite`,
      }}
    />
  );
}
