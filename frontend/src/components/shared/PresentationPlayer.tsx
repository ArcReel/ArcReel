import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type {
  PresentationReadModel,
  PresentationResourceType,
  PresentationSubtitleCue,
  PresentationVariant,
} from "@/types/presentation";
import { errMsg } from "@/utils/async";

interface PresentationPlayerProps {
  projectName: string;
  resourceType: PresentationResourceType;
  resourceId: string;
  videoVersion?: number;
  audioVersion?: number;
  posterPath?: string | null;
  initialVariant?: PresentationVariant;
  className?: string;
}

interface PresentationLoadState {
  requestKey: string;
  presentation: PresentationReadModel | null;
  error: string | null;
}

export function PresentationPlayer({
  projectName,
  resourceType,
  resourceId,
  videoVersion,
  audioVersion,
  posterPath,
  initialVariant = "post_production",
  className = "",
}: PresentationPlayerProps) {
  const { t } = useTranslation("dashboard");
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const requestEpochRef = useRef(0);
  const [variant, setVariant] = useState<PresentationVariant>(initialVariant);
  const [loadState, setLoadState] = useState<PresentationLoadState>({
    requestKey: "",
    presentation: null,
    error: null,
  });
  const [downloading, setDownloading] = useState(false);
  const [positionMicroseconds, setPositionMicroseconds] = useState(0);
  const canonicalPath =
    resourceType === "videos"
      ? `videos/scene_${resourceId}.mp4`
      : `reference_videos/${resourceId}.mp4`;
  const fingerprint = useProjectsStore((state) => state.getAssetFingerprint(canonicalPath));
  const posterFingerprint = useProjectsStore((state) =>
    posterPath ? state.getAssetFingerprint(posterPath) : null,
  );
  const requestKey = JSON.stringify([
    projectName,
    resourceType,
    resourceId,
    variant,
    videoVersion,
    audioVersion,
    fingerprint,
  ]);
  const requestMatches = loadState.requestKey === requestKey;
  const presentation = requestMatches ? loadState.presentation : null;
  const error = requestMatches ? loadState.error : null;
  const loading = !requestMatches;

  useEffect(() => {
    const epoch = ++requestEpochRef.current;
    const controller = new AbortController();
    void API.getPresentation(projectName, resourceType, resourceId, {
      variant,
      videoVersion,
      audioVersion,
      signal: controller.signal,
    })
      .then((value) => {
        if (requestEpochRef.current !== epoch) return;
        setLoadState({ requestKey, presentation: value, error: null });
        setPositionMicroseconds(0);
      })
      .catch((cause: unknown) => {
        if (
          requestEpochRef.current !== epoch ||
          (cause instanceof DOMException && cause.name === "AbortError")
        ) {
          return;
        }
        setLoadState({ requestKey, presentation: null, error: errMsg(cause) });
      });
    return () => {
      controller.abort();
      if (requestEpochRef.current === epoch) requestEpochRef.current += 1;
    };
  }, [projectName, resourceType, resourceId, variant, videoVersion, audioVersion, fingerprint, requestKey]);

  const enforceVideoAudioPolicy = useCallback(
    (video: HTMLVideoElement) => {
      if (!presentation) return;
      if (!presentation.video.audio_enabled || presentation.video.gain === 0) {
        if (video.volume !== 0) video.volume = 0;
        if (!video.muted) video.muted = true;
      }
    },
    [presentation],
  );

  useEffect(() => {
    if (videoRef.current && presentation) {
      videoRef.current.volume = presentation.video.gain;
      videoRef.current.muted = !presentation.video.audio_enabled || presentation.video.gain === 0;
      enforceVideoAudioPolicy(videoRef.current);
    }
    if (audioRef.current && presentation?.narration_audio) {
      audioRef.current.volume = presentation.narration_audio.gain;
    }
  }, [enforceVideoAudioPolicy, presentation]);

  const synchronizeNarration = useCallback(
    (play: boolean) => {
      const video = videoRef.current;
      const audio = audioRef.current;
      const narration = presentation?.narration_audio;
      if (!video || !audio || !narration) return;
      const start = narration.start_microseconds / 1_000_000;
      const duration = narration.duration_microseconds / 1_000_000;
      const audioTime = video.currentTime - start;
      if (audioTime < 0 || audioTime >= duration) {
        audio.pause();
        return;
      }
      if (Math.abs(audio.currentTime - audioTime) > 0.1) audio.currentTime = audioTime;
      if (play) void audio.play().catch(() => undefined);
    },
    [presentation],
  );

  const videoUrl = presentation
    ? API.getFileUrl(projectName, presentation.video.artifact_path, presentation.video.content_digest)
    : null;
  const narrationUrl = presentation?.narration_audio
    ? API.getFileUrl(
        projectName,
        presentation.narration_audio.artifact_path,
        presentation.narration_audio.content_digest,
      )
    : null;
  const posterUrl = posterPath
    ? API.getFileUrl(projectName, posterPath, posterFingerprint)
    : undefined;
  const captions = useMemo(
    () => (presentation ? webvttDataUrl(presentation.subtitles) : undefined),
    [presentation],
  );
  const activeCue = presentation?.subtitles.find(
    (cue) =>
      cue.start_microseconds <= positionMicroseconds &&
      positionMicroseconds < cue.start_microseconds + cue.duration_microseconds,
  );

  const chooseVariant = (next: PresentationVariant) => {
    if (next === variant) return;
    setVariant(next);
  };

  const downloadBundle = async () => {
    if (!presentation || downloading) return;
    setDownloading(true);
    try {
      const { blob, filename } = await API.downloadPresentationBundle(
        projectName,
        resourceType,
        resourceId,
        {
          variant: presentation.variant,
          videoVersion: presentation.video.version,
          audioVersion: presentation.narration_audio?.version,
        },
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      useAppStore
        .getState()
        .pushToast(t("presentation_download_failed", { message: errMsg(cause) }), "error");
    } finally {
      setDownloading(false);
    }
  };

  if (loading && !presentation) {
    return (
      <div className={`grid h-full w-full place-items-center bg-black/35 ${className}`}>
        <Loader2 className="h-5 w-5 animate-spin text-white/60" aria-label={t("presentation_loading")} />
      </div>
    );
  }

  if (!presentation || !videoUrl) {
    return (
      <div className={`grid h-full w-full place-items-center bg-black/40 p-4 text-center ${className}`}>
        <p role="alert" className="text-xs text-amber-200">
          {error || t("presentation_unavailable")}
        </p>
      </div>
    );
  }

  return (
    <div className={`relative h-full w-full bg-black ${className}`}>
      <video
        ref={videoRef}
        src={videoUrl}
        poster={posterUrl}
        aria-label={t("presentation_video_aria", { id: presentation.unit_id })}
        controls
        playsInline
        preload="metadata"
        muted={!presentation.video.audio_enabled || presentation.video.gain === 0}
        onVolumeChange={(event) => enforceVideoAudioPolicy(event.currentTarget)}
        onPlay={() => synchronizeNarration(true)}
        onPause={() => audioRef.current?.pause()}
        onSeeked={() => synchronizeNarration(!videoRef.current?.paused)}
        onTimeUpdate={(event) => {
          setPositionMicroseconds(Math.round(event.currentTarget.currentTime * 1_000_000));
          synchronizeNarration(!event.currentTarget.paused);
        }}
        onEnded={() => audioRef.current?.pause()}
        className="h-full w-full object-contain"
      >
        <track kind="captions" src={captions} srcLang="und" label={t("presentation_captions")} />
      </video>
      {narrationUrl && (
        /* eslint-disable-next-line jsx-a11y/media-has-caption -- This hidden synchronized track shares the video's visible captions. */
        <audio
          ref={audioRef}
          src={narrationUrl}
          aria-label={t("presentation_tts_track_aria", { id: presentation.unit_id })}
          preload="metadata"
          className="hidden"
        />
      )}

      {activeCue && (
        <div className="pointer-events-none absolute inset-x-3 bottom-12 flex justify-center">
          <span className="max-w-[92%] rounded-md bg-black/75 px-2.5 py-1 text-center text-xs leading-relaxed text-white shadow-lg">
            {activeCue.text}
          </span>
        </div>
      )}

      <div className="absolute inset-x-2 top-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-black/70 px-1.5 py-0.5 text-[9px] font-semibold text-white/85">
          {t(`presentation_${presentation.selection}`)}
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${
            presentation.currency === "current"
              ? "bg-emerald-950/80 text-emerald-200"
              : "bg-amber-950/85 text-amber-200"
          }`}
        >
          {t(`presentation_${presentation.currency}`)}
        </span>
        <span className="rounded bg-black/70 px-1.5 py-0.5 text-[9px] text-white/70">
          {t("presentation_mechanical_timing")}
        </span>
        <span className="flex-1" />
        {presentation.speech_mode === "narrator_voiceover" && (
          <div className="flex rounded-md bg-black/70 p-0.5">
            <VariantButton
              active={variant === "post_production"}
              label={t("presentation_post_production")}
              onClick={() => chooseVariant("post_production")}
            />
            <VariantButton
              active={variant === "use_tts"}
              label={t("presentation_use_tts")}
              onClick={() => chooseVariant("use_tts")}
            />
          </div>
        )}
        <button
          type="button"
          onClick={() => void downloadBundle()}
          disabled={downloading}
          aria-label={t("presentation_download")}
          title={t("presentation_download")}
          className="focus-ring grid h-6 w-6 place-items-center rounded-md bg-black/70 text-white/80 hover:text-white disabled:opacity-50"
        >
          {downloading ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Download className="h-3 w-3" aria-hidden />
          )}
        </button>
      </div>
    </div>
  );
}

function VariantButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${
        active ? "bg-white/20 text-white" : "text-white/55 hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}

function webvttDataUrl(cues: PresentationSubtitleCue[]): string {
  const lines = ["WEBVTT", ""];
  cues.forEach((cue, index) => {
    lines.push(
      String(index + 1),
      `${timestamp(cue.start_microseconds)} --> ${timestamp(cue.start_microseconds + cue.duration_microseconds)}`,
      cue.text,
      "",
    );
  });
  return `data:text/vtt;charset=utf-8,${encodeURIComponent(lines.join("\n"))}`;
}

function timestamp(microseconds: number): string {
  const milliseconds = Math.floor(microseconds / 1_000);
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor((milliseconds % 3_600_000) / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1_000);
  const millis = milliseconds % 1_000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
