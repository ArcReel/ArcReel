/**
 * Realtime timeline playback alternates two `<video>` elements: one plays, the other preloads the next in-point, switching at out-points;
 * before first playback load only current clips (`preload="metadata"` fetches metadata/seek frames), without next-clip preloading.
 * Narration/BGM each use one `<audio>`, aligned per-frame to global time and buffered only near current playback positions.
 * All scheduling/sync decisions come from playback-schedule/audio-sync pure functions; here only apply them to media elements.
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
  /** Whether playback started before; previously `<video>` needs only metadata/first frames, afterward full buffering/next preloading. */
  started: boolean;
  /** Current clip index in playback plans, -1 without playable clips. */
  index: number;
  /** Visible `<video>`: switch only after the next has a frame, holding the previous last frame until then. */
  visibleSlot: Slot;
  videoRefs: readonly [RefObject<HTMLVideoElement | null>, RefObject<HTMLVideoElement | null>];
  /** Stalled media pauses global clocks, picture/audio together; automatically resume when data arrives. */
  buffering: boolean;
  /** Browser blocked playback; wait for another user play click. */
  blocked: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (t: number) => void;
}

/** Fallback playhead timer interval when animation frames stall. */
const FALLBACK_TICK_MS = 200;

/** Only gaps this long since the last animation frame count as stalled, enabling fallback advancement. */
const FRAME_STALL_MS = 100;

/** Reseek only beyond this distance from in/out points, avoiding duplicate seeks on preloaded elements. */
const RESEEK_TOLERANCE = 0.05;

interface Runtime {
  index: number;
  t: number;
  slot: Slot;
  visible: Slot;
  playing: boolean;
  /** True after first playback; before then do not preload the next clip. */
  started: boolean;
  lastFrame: number;
  /** Clip ID currently loaded per element. */
  loaded: [string | null, string | null];
  /** Each element's pending seek after metadata arrival, invalidated by loading changes. */
  pendingSeek: [(() => void) | null, (() => void) | null];
  /** Clip whose next clip was preloaded; after switching wait for new picture before preloading, as idle elements still display prior last frames. */
  preloadedAfter: number;
  stallSince: number | null;
  stalled: boolean;
}

interface AudioEntry {
  element: HTMLAudioElement;
  url: string;
}

/**
 * Buffer audio according to global `at`, attaching URLs only within preload windows.
 * `preload="none"` cannot prevent fetches triggered by `play()`, so alone cannot limit unlocking on play clicks.
 */
function bufferAudio({ element: media, url }: AudioEntry, placement: AudioPlacement, at: number): void {
  const preload = audioPreload(placement, at);
  if (media.preload !== preload) media.preload = preload;
  const src = media.getAttribute("src");
  if (preload === "auto" && src !== url) media.src = url;
  // Remove old URLs changed outside windows (e.g. regenerated narration), avoiding stale-audio fetches on unlock.
  else if (preload === "none" && src !== null && src !== url) media.removeAttribute("src");
}

function blockedByPolicy(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === "NotAllowedError";
}

/**
 * Plan updates (new revisions of the same timeline) preserve position/playback state; callers remount on timeline changes.
 * @param sourceUrl Clip source-video URL, null without usable video. Require stable references; changes reload at current position.
 * @param audio Narration/BGM placement; preserve reference identity when content is unchanged.
 * @param audioUrl Source-audio URL, null if unavailable, making that segment silent. Require stable references.
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
  const [started, setStarted] = useState(false);
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
    started: false,
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
  /** Elements played once within user gestures; Safari permits later automatic audio only for these elements. */
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
      // Blocked autoplay pauses everything to prevent picture/audio divergence; other failures (unloadable media) continue as though absent.
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
      // Opening editing does not imply playback: load only current clips before first play; seek within play() supplies the next.
      if (!state.started) return;
      state.preloadedAfter = current;
      const idle: Slot = state.slot === 0 ? 1 : 0;
      element(idle)?.pause();
      const target = preloadTarget(planRef.current, current);
      if (target) load(idle, planRef.current.segments[target.index], target.sourceTime);
    },
    [element, load],
  );

  /** Display current clips only when frames arrive, then preload the next. */
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

  /** Buffer only audio playing near `at`. */
  const preloadAudioAround = useCallback((at: number) => {
    for (const placement of audioRef.current) {
      const entry = audioPool.current.get(placement.id);
      if (entry) bufferAudio(entry, placement, at);
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
      // Explicit seeking shows current elements directly; idle ones will preload other clips and cannot keep supporting the picture.
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
    // Set before seek, which preloads the next and gives idle elements URLs so this click can unlock them below.
    state.started = true;
    setStarted(true);
    setBlocked(false);
    state.lastFrame = performance.now();
    seek(state.t >= currentPlan.duration ? 0 : state.t);
    // Briefly play/pause other elements during this click so Safari later permits automatic audio at clip changes/narration starts.
    // Audio outside preload windows lacks URLs but unlocks here too; unlock follows elements, permitting automatic sound after URLs attach.
    const idle = element(state.slot === 0 ? 1 : 0);
    const others: HTMLMediaElement[] = [
      ...(idle?.getAttribute("src") ? [idle] : []),
      ...Array.from(audioPool.current.values(), (entry) => entry.element),
    ];
    for (const media of others) {
      if (unlocked.current.has(media)) continue;
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
      // react-doctor-disable-next-line react-doctor/no-pass-data-to-parent -- audioUrl is a caller-provided pure URL lookup, not a reporting callback; this effect synchronizes the external Audio element pool
      const url = audioUrl(placement);
      if (!url) continue;
      wanted.add(placement.id);
      const entry = pool.get(placement.id);
      if (entry?.url === url) continue;
      const next = { element: entry?.element ?? new Audio(), url };
      pool.set(placement.id, next);
      bufferAudio(next, placement, runtime.current.t);
    }
    for (const [id, entry] of pool) {
      if (wanted.has(id)) continue;
      entry.element.pause();
      entry.element.removeAttribute("src");
      pool.delete(id);
    }
  }, [audio, audioUrl]);

  useEffect(() => {
    // Paused seeks also switch after new frames arrive; during playback per-frame advancement handles it.
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
          // At out-points, hold last frames and advance freeze extensions by wall clock.
          video.pause();
        }
        refreshVisible();
      }
      syncAudioTo(result.t, true);
      setT(result.t);
    };
    let lastFrameAt = performance.now();
    const onFrame = (now: number) => {
      lastFrameAt = performance.now();
      frame = requestAnimationFrame(onFrame);
      step(now);
    };
    frame = requestAnimationFrame(onFrame);
    // Background tabs stall animation frames while videos play; low-frequency timers prevent crossing out-points, without double advancement when frames work.
    const fallback = window.setInterval(() => {
      const now = performance.now();
      if (now - lastFrameAt >= FRAME_STALL_MS) step(now);
    }, FALLBACK_TICK_MS);
    return () => {
      cancelAnimationFrame(frame);
      window.clearInterval(fallback);
    };
  }, [element, playing, refreshVisible, startMedia, stopAtEnd, switchTo, syncAudioTo]);

  useEffect(() => {
    const videos = [refA.current, refB.current];
    const pool = audioPool.current;
    // After removing URLs, load() discards buffered media immediately instead of waiting for element collection.
    const release = (media: HTMLMediaElement) => {
      media.pause();
      media.removeAttribute("src");
      media.load();
    };
    return () => {
      videos.forEach((video) => {
        if (video) release(video);
      });
      pool.forEach(({ element: media }) => release(media));
      pool.clear();
    };
  }, []);

  return {
    t,
    playing,
    started,
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
