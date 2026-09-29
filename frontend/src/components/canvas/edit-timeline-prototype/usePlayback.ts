// PROTOTYPE（#2752）：浏览器端按剪辑时间线实时拼接播放。
// 两个 <video> 交替：当前片段播放时，另一个预载下一片段的入点；到出点切换并按转场时长做淡入。
// 旁白与 BGM 各自一个 <audio>，每帧按全局时间校准。转场只做淡入近似，不模拟具体效果。
import { useCallback, useEffect, useRef, useState } from "react";

import { clipAt, type ResolvedClip, type ResolvedTimeline } from "./model";

export interface Playback {
  t: number;
  playing: boolean;
  current: ResolvedClip | undefined;
  activeSlot: 0 | 1;
  fade: { slot: 0 | 1; duration: number; label: string } | null;
  videoRefs: [React.RefObject<HTMLVideoElement | null>, React.RefObject<HTMLVideoElement | null>];
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (t: number) => void;
}

export function usePlayback(r: ResolvedTimeline): Playback {
  const refA = useRef<HTMLVideoElement>(null);
  const refB = useRef<HTMLVideoElement>(null);
  const slots = [refA, refB] as const;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [activeSlot, setActiveSlot] = useState<0 | 1>(0);
  const [fade, setFade] = useState<Playback["fade"]>(null);

  const st = useRef<{ idx: number; slot: 0 | 1; playing: boolean; t: number; fadeUntil: number; preloadPending: boolean }>({ idx: 0, slot: 0, playing: false, t: 0, fadeUntil: 0, preloadPending: false });
  const audios = useRef<Map<string, HTMLAudioElement>>(new Map());

  const el = (slot: 0 | 1) => slots[slot].current;

  const load = useCallback((slot: 0 | 1, clip: ResolvedClip | undefined, offset: number) => {
    const v = slots[slot].current;
    if (!v || !clip) return;
    const url = new URL(clip.unit.src, window.location.origin).href;
    if (v.src !== url) v.src = url;
    v.currentTime = clip.effIn + offset;
    v.volume = clip.clip.volume;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const audioFor = (src: string) => {
    let a = audios.current.get(src);
    if (!a) {
      a = new Audio(src);
      a.preload = "auto";
      audios.current.set(src, a);
    }
    return a;
  };

  const syncAudio = useCallback(
    (gt: number, isPlaying: boolean) => {
      const tracks = r.narrations.map((n) => ({ src: n.src, start: n.start, end: n.end, volume: 1 }));
      if (r.timeline.bgm) {
        const b = r.timeline.bgm;
        const end = Math.min(b.end, r.total);
        const fadeOut = Math.max(0, Math.min(1, (end - gt) / 2));
        const fadeIn = Math.max(0, Math.min(1, (gt - b.start) / 1));
        // 旁白期间 BGM 压低（ducking 规则未定，这里只是示意）
        const ducked = r.narrations.some((n) => gt >= n.start && gt < n.end) ? 0.4 : 1;
        tracks.push({ src: b.src, start: b.start, end, volume: b.volume * fadeIn * fadeOut * ducked });
      }
      for (const tr of tracks) {
        const a = audioFor(tr.src);
        const inside = gt >= tr.start && gt < tr.end;
        a.volume = Math.max(0, Math.min(1, tr.volume));
        if (inside && isPlaying) {
          const want = gt - tr.start;
          if (a.paused) {
            a.currentTime = want;
            void a.play().catch(() => {});
          } else if (Math.abs(a.currentTime - want) > 0.3) {
            a.currentTime = want;
          }
        } else if (!a.paused) {
          a.pause();
        }
      }
    },
    [r],
  );

  const seek = useCallback(
    (gt: number) => {
      const clamped = Math.max(0, Math.min(gt, Math.max(0, r.total - 0.01)));
      const clip = clipAt(r, clamped);
      if (!clip) return;
      const s = st.current;
      s.idx = r.playable.indexOf(clip);
      s.t = clamped;
      s.fadeUntil = 0;
      setFade(null);
      load(s.slot, clip, clamped - clip.start);
      load((1 - s.slot) as 0 | 1, r.playable[s.idx + 1], 0);
      el((1 - s.slot) as 0 | 1)?.pause();
      if (s.playing) void el(s.slot)?.play().catch(() => {});
      setT(clamped);
      syncAudio(clamped, s.playing);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [r, load, syncAudio],
  );

  const play = useCallback(() => {
    const s = st.current;
    if (s.t >= r.total - 0.05) seek(0);
    s.playing = true;
    setPlaying(true);
    void el(s.slot)?.play().catch(() => {});
    syncAudio(s.t, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r, seek, syncAudio]);

  const pause = useCallback(() => {
    const s = st.current;
    s.playing = false;
    setPlaying(false);
    el(s.slot)?.pause();
    syncAudio(s.t, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncAudio]);

  // 换时间线时回到开头
  useEffect(() => {
    st.current.playing = false;
    setPlaying(false);
    el(0)?.pause();
    el(1)?.pause();
    st.current.slot = 0;
    setActiveSlot(0);
    seek(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r]);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const s = st.current;
      if (!s.playing) return;
      const clip = r.playable[s.idx];
      const v = el(s.slot);
      if (!clip || !v) return;
      const local = v.currentTime;
      if (local >= clip.effOut - 0.03 || v.ended) {
        const next = r.playable[s.idx + 1];
        if (!next) {
          s.playing = false;
          s.t = r.total;
          setPlaying(false);
          setT(r.total);
          v.pause();
          syncAudio(r.total, false);
          return;
        }
        const nextSlot = (1 - s.slot) as 0 | 1;
        const nv = el(nextSlot);
        if (nv) {
          if (Math.abs(nv.currentTime - next.effIn) > 0.1) load(nextSlot, next, 0);
          void nv.play().catch(() => {});
        }
        v.pause();
        const tr = clip.transitionOut;
        const gap = r.clips.slice(clip.index + 1, next.index).some((c) => c.missing);
        if (tr && !gap) {
          setFade({ slot: nextSlot, duration: tr.duration, label: `${tr.kind} ${tr.duration}s` });
          s.fadeUntil = performance.now() + tr.duration * 1000 + 50;
        } else {
          setFade(null);
          s.fadeUntil = performance.now();
        }
        s.preloadPending = true;
        s.idx += 1;
        s.slot = nextSlot;
        setActiveSlot(nextSlot);
        return;
      }
      if (s.preloadPending && performance.now() >= s.fadeUntil) {
        s.preloadPending = false;
        setFade(null);
        load((1 - s.slot) as 0 | 1, r.playable[s.idx + 1], 0);
      }
      s.t = clip.start + Math.max(0, local - clip.effIn);
      setT(s.t);
      syncAudio(s.t, true);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r, load, syncAudio]);

  useEffect(() => {
    const map = audios.current;
    return () => map.forEach((a) => a.pause());
  }, []);

  const current = clipAt(r, t);

  return {
    t,
    playing,
    current,
    activeSlot,
    fade,
    videoRefs: [refA, refB],
    play,
    pause,
    toggle: () => (st.current.playing ? pause() : play()),
    seek,
  };
}
