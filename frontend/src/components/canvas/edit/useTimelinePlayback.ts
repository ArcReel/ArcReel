/**
 * 按剪辑时间线实时拼接播放：两个 `<video>` 交替，一个在播，另一个预载下一个片段的入点，到出点时切换。
 * 调度结论全部来自 playback-schedule 的纯函数；这里只负责把结论落到媒体元素上。
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import {
  advancePlayhead,
  locate,
  preloadTarget,
  type PlaybackPlan,
  type PlaybackSegment,
} from "./playback-schedule";

type Slot = 0 | 1;

export interface TimelinePlayback {
  t: number;
  playing: boolean;
  /** 当前片段在播放计划里的下标；没有可播放片段时为 -1。 */
  index: number;
  activeSlot: Slot;
  videoRefs: readonly [RefObject<HTMLVideoElement | null>, RefObject<HTMLVideoElement | null>];
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (t: number) => void;
}

/** 动画帧停摆时推进播放头的兜底间隔。 */
const FALLBACK_TICK_MS = 200;

/** 与出点、入点的差距超过这个值才重新定位，避免对已预载好的元素重复 seek。 */
const RESEEK_TOLERANCE = 0.05;

interface Runtime {
  index: number;
  t: number;
  slot: Slot;
  playing: boolean;
  lastFrame: number;
  /** 每个元素当前装载的片段 ID。 */
  loaded: [string | null, string | null];
  /** 每个元素等待元数据到达后要定位到的时间；换装载时作废。 */
  pendingSeek: [(() => void) | null, (() => void) | null];
}

/**
 * 计划更新（同一条剪辑时间线出了新修订）时保留当前位置与播放状态；切换剪辑时间线由调用方重新挂载。
 * @param sourceUrl 片段的源视频地址；没有可用视频时返回 null。须传稳定引用，变化时按当前位置重新装载。
 */
export function useTimelinePlayback(
  plan: PlaybackPlan,
  sourceUrl: (segment: PlaybackSegment) => string | null,
): TimelinePlayback {
  const refA = useRef<HTMLVideoElement>(null);
  const refB = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [index, setIndex] = useState(-1);
  const [activeSlot, setActiveSlot] = useState<Slot>(0);
  const runtime = useRef<Runtime>({
    index: -1,
    t: 0,
    slot: 0,
    playing: false,
    lastFrame: 0,
    loaded: [null, null],
    pendingSeek: [null, null],
  });
  const planRef = useRef(plan);

  const element = useCallback((slot: Slot) => (slot === 0 ? refA.current : refB.current), []);

  const cancelPendingSeek = useCallback(
    (slot: Slot) => {
      const pending = runtime.current.pendingSeek[slot];
      if (pending) element(slot)?.removeEventListener("loadedmetadata", pending);
      runtime.current.pendingSeek[slot] = null;
    },
    [element],
  );

  const load = useCallback(
    (slot: Slot, segment: PlaybackSegment, sourceTime: number) => {
      const video = element(slot);
      const url = sourceUrl(segment);
      if (!video || !url) return;
      cancelPendingSeek(slot);
      if (video.getAttribute("src") !== url) video.src = url;
      video.volume = segment.sourceVolume;
      runtime.current.loaded[slot] = segment.clipId;
      if (video.readyState >= HTMLMediaElement.HAVE_METADATA) {
        if (Math.abs(video.currentTime - sourceTime) > RESEEK_TOLERANCE) video.currentTime = sourceTime;
        return;
      }
      const onMetadata = () => {
        runtime.current.pendingSeek[slot] = null;
        video.currentTime = sourceTime;
      };
      runtime.current.pendingSeek[slot] = onMetadata;
      video.addEventListener("loadedmetadata", onMetadata, { once: true });
    },
    [cancelPendingSeek, element, sourceUrl],
  );

  const startVideo = useCallback(
    (slot: Slot) => {
      // 自动播放被拒时停在当前帧，播放头按墙钟继续推进，不中断整条时间线。
      void element(slot)?.play()?.catch(() => {});
    },
    [element],
  );

  const preloadAfter = useCallback(
    (current: number) => {
      const state = runtime.current;
      const idle: Slot = state.slot === 0 ? 1 : 0;
      element(idle)?.pause();
      const target = preloadTarget(planRef.current, current);
      if (target) load(idle, planRef.current.segments[target.index], target.sourceTime);
    },
    [element, load],
  );

  const seek = useCallback(
    (to: number) => {
      const state = runtime.current;
      const currentPlan = planRef.current;
      const location = locate(currentPlan, to);
      if (!location) {
        state.index = -1;
        state.t = 0;
        element(0)?.pause();
        element(1)?.pause();
        setIndex(-1);
        setT(0);
        return;
      }
      const segment = currentPlan.segments[location.index];
      state.index = location.index;
      state.t = Math.min(Math.max(to, 0), currentPlan.duration);
      const active = element(state.slot);
      if (location.sourceTime !== null) load(state.slot, segment, location.sourceTime);
      if (state.playing && segment.hasVideo && !location.holding) startVideo(state.slot);
      else active?.pause();
      preloadAfter(location.index);
      setIndex(location.index);
      setT(state.t);
    },
    [element, load, preloadAfter, startVideo],
  );

  const switchTo = useCallback(
    (next: number) => {
      const state = runtime.current;
      const segment = planRef.current.segments[next];
      const previous = state.slot;
      state.index = next;
      if (segment.hasVideo) {
        const incoming: Slot = previous === 0 ? 1 : 0;
        if (state.loaded[incoming] !== segment.clipId) load(incoming, segment, segment.sourceIn);
        state.slot = incoming;
        if (state.playing) startVideo(incoming);
        element(previous)?.pause();
        setActiveSlot(incoming);
        preloadAfter(next);
      } else {
        element(previous)?.pause();
      }
      setIndex(next);
    },
    [element, load, preloadAfter, startVideo],
  );

  const stopAtEnd = useCallback(() => {
    const state = runtime.current;
    state.playing = false;
    state.t = planRef.current.duration;
    element(0)?.pause();
    element(1)?.pause();
    setPlaying(false);
    setT(state.t);
  }, [element]);

  const play = useCallback(() => {
    const state = runtime.current;
    const currentPlan = planRef.current;
    if (currentPlan.segments.length === 0) return;
    state.playing = true;
    setPlaying(true);
    state.lastFrame = performance.now();
    seek(state.t >= currentPlan.duration ? 0 : state.t);
  }, [seek]);

  const pause = useCallback(() => {
    const state = runtime.current;
    state.playing = false;
    setPlaying(false);
    element(state.slot)?.pause();
  }, [element]);

  useEffect(() => {
    planRef.current = plan;
    runtime.current.loaded = [null, null];
    seek(runtime.current.t);
  }, [plan, seek]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const step = (now: number) => {
      const state = runtime.current;
      const currentPlan = planRef.current;
      const segment = currentPlan.segments[state.index];
      if (!state.playing || !segment) return;
      const elapsed = Math.max(0, (now - state.lastFrame) / 1000);
      state.lastFrame = now;
      const video = element(state.slot);
      const videoAdvancing =
        segment.hasVideo && video && state.loaded[state.slot] === segment.clipId && !video.paused && !video.ended;
      const result = advancePlayhead(
        currentPlan,
        { index: state.index, t: state.t },
        { videoTime: videoAdvancing ? video.currentTime : null, elapsed },
      );
      if (result.ended) {
        stopAtEnd();
        return;
      }
      state.t = result.t;
      if (result.index !== state.index) {
        switchTo(result.index);
      } else if (result.holding && video && !video.paused) {
        // 画面到出点后停在末帧，定格延长期间按墙钟推进。
        video.pause();
      }
      setT(result.t);
    };
    const onFrame = (now: number) => {
      frame = requestAnimationFrame(onFrame);
      step(now);
    };
    frame = requestAnimationFrame(onFrame);
    // 后台标签页里动画帧停摆而视频照常播放，低频定时器兜底推进，片段不会越过出点。
    const fallback = window.setInterval(() => step(performance.now()), FALLBACK_TICK_MS);
    return () => {
      cancelAnimationFrame(frame);
      window.clearInterval(fallback);
    };
  }, [element, playing, stopAtEnd, switchTo]);

  useEffect(() => {
    const videos = [refA.current, refB.current];
    return () => videos.forEach((video) => video?.pause());
  }, []);

  return {
    t,
    playing,
    index,
    activeSlot,
    videoRefs: [refA, refB],
    play,
    pause,
    toggle: () => (runtime.current.playing ? pause() : play()),
    seek,
  };
}
