/**
 * 按剪辑时间线实时拼接播放：两个 `<video>` 交替，一个在播，另一个预载下一个片段的入点，到出点时切换；
 * 旁白与 BGM 各用一个 `<audio>`，每一帧按全局时钟对齐，只缓冲当前位置附近要放的音频。
 * 调度与同步的结论全部来自 playback-schedule 与 audio-sync 的纯函数；这里只负责把结论落到媒体元素上。
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { audioPreload, mediaWaiting, syncAudio, trackStall } from "./audio-sync";
import {
  advancePlayhead,
  locate,
  preloadTarget,
  type PlaybackPlan,
  type PlaybackSegment,
} from "./playback-schedule";
import type { AudioPlacement } from "./preview-tracks";

type Slot = 0 | 1;

export interface TimelinePlayback {
  t: number;
  playing: boolean;
  /** 当前片段在播放计划里的下标；没有可播放片段时为 -1。 */
  index: number;
  /** 正在显示的 `<video>`。切到下一个片段时，新片段有了画面才换过去，之前停在上一个片段的末帧上。 */
  visibleSlot: Slot;
  videoRefs: readonly [RefObject<HTMLVideoElement | null>, RefObject<HTMLVideoElement | null>];
  /** 某段媒体缓冲卡住：全局时钟停住，画面与音频一起暂停，数据到了自动继续。 */
  buffering: boolean;
  /** 浏览器拦截了播放，等用户再点一次播放。 */
  blocked: boolean;
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
  visible: Slot;
  playing: boolean;
  lastFrame: number;
  /** 每个元素当前装载的片段 ID。 */
  loaded: [string | null, string | null];
  /** 每个元素等待元数据到达后要定位到的时间；换装载时作废。 */
  pendingSeek: [(() => void) | null, (() => void) | null];
  /** 已为哪个片段预载过下一段；切换后等新片段有了画面再预载，空闲元素此前还在显示上一段的末帧。 */
  preloadedAfter: number;
  stallSince: number | null;
  stalled: boolean;
}

interface AudioEntry {
  element: HTMLAudioElement;
  url: string;
}

function blockedByPolicy(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === "NotAllowedError";
}

/**
 * 计划更新（同一条剪辑时间线出了新修订）时保留当前位置与播放状态；切换剪辑时间线由调用方重新挂载。
 * @param sourceUrl 片段的源视频地址；没有可用视频时返回 null。须传稳定引用，变化时按当前位置重新装载。
 * @param audio 旁白与 BGM 的摆放；内容不变时须保持同一引用。
 * @param audioUrl 音频的源地址；拿不到时返回 null，这段音频不出声。须传稳定引用。
 */
export function useTimelinePlayback(
  plan: PlaybackPlan,
  sourceUrl: (segment: PlaybackSegment) => string | null,
  audio: readonly AudioPlacement[],
  audioUrl: (placement: AudioPlacement) => string | null,
): TimelinePlayback {
  const refA = useRef<HTMLVideoElement>(null);
  const refB = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [index, setIndex] = useState(-1);
  const [visibleSlot, setVisibleSlot] = useState<Slot>(0);
  const [buffering, setBuffering] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const runtime = useRef<Runtime>({
    index: -1,
    t: 0,
    slot: 0,
    visible: 0,
    playing: false,
    lastFrame: 0,
    loaded: [null, null],
    pendingSeek: [null, null],
    preloadedAfter: -1,
    stallSince: null,
    stalled: false,
  });
  const planRef = useRef(plan);
  const audioRef = useRef(audio);
  const audioPool = useRef(new Map<string, AudioEntry>());
  /** 在用户手势里播放过一次的媒体元素；Safari 只允许这样的元素之后自动出声。 */
  const unlocked = useRef(new WeakSet<HTMLMediaElement>());

  const element = useCallback((slot: Slot) => (slot === 0 ? refA.current : refB.current), []);

  const pauseAudio = useCallback(() => {
    audioPool.current.forEach(({ element: audioElement }) => audioElement.pause());
  }, []);

  const clearStall = useCallback(() => {
    const state = runtime.current;
    state.stallSince = null;
    state.stalled = false;
    setBuffering(false);
  }, []);

  const halt = useCallback(() => {
    const state = runtime.current;
    state.playing = false;
    setPlaying(false);
    clearStall();
    element(0)?.pause();
    element(1)?.pause();
    pauseAudio();
  }, [clearStall, element, pauseAudio]);

  const onPlayRejected = useCallback(
    (error: unknown) => {
      // 自动播放被拦截时整体暂停，避免画面和声音各走各的；其他失败（素材加载不了）按没有这段媒体继续。
      if (!blockedByPolicy(error) || !runtime.current.playing) return;
      halt();
      setBlocked(true);
    },
    [halt],
  );

  const startMedia = useCallback(
    (media: HTMLMediaElement | null) => {
      void media?.play()?.catch(onPlayRejected);
    },
    [onPlayRejected],
  );

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

  const preloadAfter = useCallback(
    (current: number) => {
      const state = runtime.current;
      state.preloadedAfter = current;
      const idle: Slot = state.slot === 0 ? 1 : 0;
      element(idle)?.pause();
      const target = preloadTarget(planRef.current, current);
      if (target) load(idle, planRef.current.segments[target.index], target.sourceTime);
    },
    [element, load],
  );

  /** 当前片段有了画面才把它显示出来，并在此之后为下一段预载。 */
  const refreshVisible = useCallback(() => {
    const state = runtime.current;
    const segment = planRef.current.segments[state.index];
    if (!segment) return;
    const video = element(state.slot);
    const ready =
      !segment.hasVideo ||
      (video !== null &&
        state.loaded[state.slot] === segment.clipId &&
        video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
        !video.seeking);
    if (!ready) return;
    if (state.visible !== state.slot) {
      state.visible = state.slot;
      setVisibleSlot(state.slot);
    }
    if (state.preloadedAfter !== state.index) preloadAfter(state.index);
  }, [element, preloadAfter]);

  /** 只缓冲 `at` 附近要放的音频。 */
  const preloadAudioAround = useCallback((at: number) => {
    for (const placement of audioRef.current) {
      const media = audioPool.current.get(placement.id)?.element;
      const preload = audioPreload(placement, at);
      if (media && media.preload !== preload) media.preload = preload;
    }
  }, []);

  const syncAudioTo = useCallback((at: number, running: boolean) => {
    preloadAudioAround(at);
    for (const placement of audioRef.current) {
      const media = audioPool.current.get(placement.id)?.element;
      if (!media) continue;
      const directive = syncAudio(placement, at, running, {
        currentTime: media.currentTime,
        paused: media.paused,
        seeking: media.seeking,
        ended: media.ended,
      });
      if (directive.seekTo !== null) media.currentTime = directive.seekTo;
      if (Math.abs(media.playbackRate - directive.playbackRate) > 1e-3) media.playbackRate = directive.playbackRate;
      if (Math.abs(media.volume - directive.volume) > 1e-3) media.volume = directive.volume;
      if (directive.play && media.paused) startMedia(media);
      else if (!directive.play && !media.paused) media.pause();
    }
  }, [preloadAudioAround, startMedia]);

  const seek = useCallback(
    (to: number) => {
      const state = runtime.current;
      const currentPlan = planRef.current;
      const location = locate(currentPlan, to);
      state.stallSince = null;
      if (!location) {
        state.index = -1;
        state.t = 0;
        element(0)?.pause();
        element(1)?.pause();
        pauseAudio();
        setIndex(-1);
        setT(0);
        return;
      }
      const segment = currentPlan.segments[location.index];
      state.index = location.index;
      state.t = Math.min(Math.max(to, 0), currentPlan.duration);
      const active = element(state.slot);
      // 主动跳转直接显示当前元素：空闲元素接下来要预载别的片段，不能再拿它撑画面。
      state.visible = state.slot;
      setVisibleSlot(state.slot);
      if (location.sourceTime !== null) load(state.slot, segment, location.sourceTime);
      if (state.playing && segment.hasVideo && !location.holding) startMedia(active);
      else active?.pause();
      preloadAfter(location.index);
      preloadAudioAround(state.t);
      setIndex(location.index);
      setT(state.t);
    },
    [element, load, pauseAudio, preloadAfter, preloadAudioAround, startMedia],
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
        if (state.playing) startMedia(element(incoming));
        element(previous)?.pause();
      } else {
        element(previous)?.pause();
      }
      refreshVisible();
      setIndex(next);
    },
    [element, load, refreshVisible, startMedia],
  );

  const stopAtEnd = useCallback(() => {
    const state = runtime.current;
    halt();
    state.t = planRef.current.duration;
    setT(state.t);
  }, [halt]);

  const play = useCallback(() => {
    const state = runtime.current;
    const currentPlan = planRef.current;
    if (currentPlan.segments.length === 0) return;
    state.playing = true;
    setPlaying(true);
    setBlocked(false);
    state.lastFrame = performance.now();
    seek(state.t >= currentPlan.duration ? 0 : state.t);
    // 趁这次点击把其余媒体元素各播一下再停住：Safari 之后才允许它们在切换片段、旁白开始时自动出声。
    const others: (HTMLMediaElement | null)[] = [
      element(state.slot === 0 ? 1 : 0),
      ...Array.from(audioPool.current.values(), (entry) => entry.element),
    ];
    for (const media of others) {
      if (!media || unlocked.current.has(media) || !media.getAttribute("src")) continue;
      unlocked.current.add(media);
      const attempt = media.play();
      media.pause();
      void attempt?.catch(() => {});
    }
    const active = element(state.slot);
    if (active) unlocked.current.add(active);
  }, [element, seek]);

  const pause = useCallback(() => {
    halt();
  }, [halt]);

  useEffect(() => {
    planRef.current = plan;
    runtime.current.loaded = [null, null];
    seek(runtime.current.t);
  }, [plan, seek]);

  useEffect(() => {
    audioRef.current = audio;
    const pool = audioPool.current;
    const wanted = new Set<string>();
    for (const placement of audio) {
      const url = audioUrl(placement);
      if (!url) continue;
      wanted.add(placement.id);
      const entry = pool.get(placement.id);
      if (entry?.url === url) continue;
      const media = entry?.element ?? new Audio();
      media.preload = audioPreload(placement, runtime.current.t);
      media.src = url;
      pool.set(placement.id, { element: media, url });
    }
    for (const [id, entry] of pool) {
      if (wanted.has(id)) continue;
      entry.element.pause();
      entry.element.removeAttribute("src");
      pool.delete(id);
    }
  }, [audio, audioUrl]);

  useEffect(() => {
    // 暂停时跳转也要在新片段有画面后切过去；播放中由每一帧的推进负责。
    const videos = [refA.current, refB.current];
    videos.forEach((video) => {
      video?.addEventListener("loadeddata", refreshVisible);
      video?.addEventListener("seeked", refreshVisible);
    });
    return () =>
      videos.forEach((video) => {
        video?.removeEventListener("loadeddata", refreshVisible);
        video?.removeEventListener("seeked", refreshVisible);
      });
  }, [refreshVisible]);

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
      const holding = locate(currentPlan, state.t)?.holding ?? false;
      const inPicture =
        segment.hasVideo && !holding && video !== null && state.loaded[state.slot] === segment.clipId;

      const videoWaiting =
        inPicture &&
        mediaWaiting({
          readyState: video.readyState,
          seeking: video.seeking,
          errored: video.error !== null,
          ended: video.ended,
        });
      const audioWaiting = audioRef.current.some((placement) => {
        const media = audioPool.current.get(placement.id)?.element;
        return (
          media !== undefined &&
          state.t >= placement.start &&
          state.t < placement.end &&
          mediaWaiting({
            readyState: media.readyState,
            seeking: media.seeking,
            errored: media.error !== null,
            ended: media.ended,
          })
        );
      });
      const stall = trackStall(state.stallSince, videoWaiting || audioWaiting, now);
      state.stallSince = stall.since;
      if (stall.stalled) {
        if (!state.stalled) {
          state.stalled = true;
          setBuffering(true);
          video?.pause();
          syncAudioTo(state.t, false);
        }
        refreshVisible();
        return;
      }
      if (state.stalled) {
        state.stalled = false;
        setBuffering(false);
        if (inPicture) startMedia(video);
      }

      const videoAdvancing = inPicture && !video.paused && !video.ended;
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
      } else {
        if (result.holding && video && !video.paused) {
          // 画面到出点后停在末帧，定格延长期间按墙钟推进。
          video.pause();
        }
        refreshVisible();
      }
      syncAudioTo(result.t, true);
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
  }, [element, playing, refreshVisible, startMedia, stopAtEnd, switchTo, syncAudioTo]);

  useEffect(() => {
    const videos = [refA.current, refB.current];
    const pool = audioPool.current;
    return () => {
      videos.forEach((video) => video?.pause());
      pool.forEach(({ element: media }) => {
        media.pause();
        media.removeAttribute("src");
      });
      pool.clear();
    };
  }, []);

  return {
    t,
    playing,
    index,
    visibleSlot,
    videoRefs: [refA, refB],
    buffering,
    blocked,
    play,
    pause,
    toggle: () => (runtime.current.playing ? pause() : play()),
    seek,
  };
}
