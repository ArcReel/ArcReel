import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "cn";

export interface TypewriterSegment {
  text: string;
  className?: string;
  after?: ReactNode;
}

/** Caret blinking after text completes, matching index.css `--animate-caret-linger` (1.4s × 3). */
const CARET_LINGER_MS = 1400 * 3;

interface TypewriterProps {
  segments: TypewriterSegment[];
  speed?: number;
  punctuationDelay?: number;
  startDelay?: number;
  segmentGap?: number;
  caret?: boolean;
  /** Same once keys play once per session; later mounts show completion directly. */
  once?: string;
  className?: string;
  onDone?: () => void;
}

const PUNCTUATION = /[，。！？；：、,.!?;:…—]/;
const playedOnce = new Set<string>();

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Typewriter effect plays segments sequentially; append `after` (for example <br />) after first segments before starting second ones.
 * Punctuation/paragraph boundaries automatically lengthen pauses, with cinematic editing-machine pacing.
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
  onDone,
}: TypewriterProps) {
  const totalLen = useMemo(() => segments.reduce((n, s) => n + s.text.length, 0), [segments]);
  const fullText = useMemo(() => segments.map((s) => s.text).join(""), [segments]);

  const skip =
    totalLen === 0 || prefersReducedMotion() || (once ? playedOnce.has(once) : false);

  const [pos, setPos] = useState(skip ? totalLen : 0);
  const [phase, setPhase] = useState<"typing" | "linger" | "done">(skip ? "done" : "typing");
  // Reset progress immediately on copy/skip changes, preventing new text truncation or stuck done.
  // (React 19 recommended render-time setState: snapshot prev, synchronously setState on differences to rerender.)
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

  // Capture segments in refs: callers often construct inline, creating new references on every render;
  // effect dependencies on segments make unrelated parent rerenders clear timers and restart tick(0),
  // with later setPos(next) moving pos back to first characters. fullText is the true content key.
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
          }, CARET_LINGER_MS),
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
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- fixed-order text segments never reorder/add/remove; positions are identities, repeated text cannot be keys
      <span key={`tw-s-${i}`} className={seg.className}>
        {seg.text.slice(0, visibleEnd - consumed)}
      </span>,
    );
    if (seg.after && pos >= segEnd) {
      visibleNodes.push(<span key={`tw-a-${i}`}>{seg.after}</span>);
    }
    consumed = segEnd;
  });

  return (
    <span className={className}>
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
      className={cn(
        "ml-0.5 inline-block h-[1em] w-1 rounded-xs bg-primary align-[-0.12em]",
        finite ? "animate-caret-linger" : "animate-caret-blink",
      )}
    />
  );
}
