import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AudioLines, Pause, Play } from "lucide-react";
import { enqueueCharacterVoiceSample } from "@/actions/generation";
import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { isResourceBusy, useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";
import { errMsg } from "@/utils/async";

const VOICE_SAMPLE_TEXT_MAX_LENGTH = 200;
type Strategy = "video" | "tts";

interface VoiceOption {
  id: string;
  label: string;
}

interface VoiceSampleButtonProps {
  projectName: string;
  characterName: string;
  /** Kept for card compatibility; voice and image generation intentionally do not share a busy slot. */
  busy?: boolean;
  onSaved: () => Promise<unknown> | void;
  initialVoiceId?: string;
}

/** Generate, preview, and explicitly promote a character reference-audio candidate. */
export function VoiceSampleButton({
  projectName,
  characterName,
  onSaved,
  initialVoiceId,
}: VoiceSampleButtonProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [strategy, setStrategy] = useState<Strategy>("video");
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [voicesConfigured, setVoicesConfigured] = useState(true);
  const [voices, setVoices] = useState<VoiceOption[]>([]);
  const [selectedVoice, setSelectedVoice] = useState("");
  const [text, setText] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [lastSeenTask, setLastSeenTask] = useState<TaskItem | null>(null);
  const [localSubmitting, setLocalSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [isPreviewPlaying, setIsPreviewPlaying] = useState(false);
  const previewAudioRef = useRef<HTMLAudioElement>(null);
  const titleId = useId();
  const descId = useId();
  const voiceFieldId = useId();
  const textFieldId = useId();

  const availableMediaTypes = useConfigStatusStore((s) => s.availableMediaTypes);
  const videoConfigured = availableMediaTypes.includes("video");
  const audioConfigured = availableMediaTypes.includes("audio");
  const disabled = !videoConfigured && !audioConfigured;

  const liveTask = useTasksStore((s) => (taskId ? s.tasks.find((item) => item.task_id === taskId) : undefined));
  useEffect(() => {
    if (taskId == null) return;
    const captureIfPresent = (tasks: TaskItem[]) => {
      const found = tasks.find((item) => item.task_id === taskId);
      if (found) setLastSeenTask(found);
    };
    const unsubscribe = useTasksStore.subscribe((state) => captureIfPresent(state.tasks));
    void Promise.resolve().then(() => captureIfPresent(useTasksStore.getState().tasks));
    return unsubscribe;
  }, [taskId]);
  const task = liveTask ?? (lastSeenTask?.task_id === taskId ? lastSeenTask : undefined);
  const generating =
    localSubmitting ||
    (taskId != null && (task == null || task.status === "queued" || task.status === "running" || task.status === "cancelling"));
  const failed = task?.status === "failed";
  const succeeded = task?.status === "succeeded";
  const previewFilePath =
    succeeded && task.result && typeof task.result.file_path === "string" ? task.result.file_path : null;
  const previewUrl = previewFilePath ? API.getFileUrl(projectName, previewFilePath, taskId ?? undefined) : null;

  // Discover candidates created automatically at inventory completion. The private
  // source video is never returned; this endpoint exposes only the derived WAV task.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    API.getCharacterVoiceSampleCandidate(projectName, characterName, { signal: controller.signal })
      .then(({ candidate }) => {
        if (controller.signal.aborted || candidate == null) return;
        setTaskId(candidate.task_id);
        setLastSeenTask(candidate);
        const candidateStrategy = candidate.payload.strategy;
        if (candidateStrategy === "video" || candidateStrategy === "tts") {
          if (candidateStrategy === "tts") setVoicesLoading(true);
          setStrategy(candidateStrategy);
        }
        const candidateText =
          typeof candidate.payload.monologue === "string"
            ? candidate.payload.monologue
            : typeof candidate.payload.prompt === "string"
              ? candidate.payload.prompt
              : null;
        if (candidateText) setText(candidateText);
        if (typeof candidate.payload.voice === "string") setSelectedVoice(candidate.payload.voice);
      })
      .catch((error) => {
        if (!controller.signal.aborted) useAppStore.getState().pushToast(errMsg(error), "error");
      });
    return () => controller.abort();
  }, [characterName, open, projectName]);

  // TTS voices are optional and loaded only when the fallback mode is selected.
  useEffect(() => {
    if (!open || strategy !== "tts") return;
    const controller = new AbortController();
    API.getAudioBackendVoices(projectName, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setVoicesConfigured(res.configured);
        setVoices(res.voices);
        setSelectedVoice((previous) =>
          previous || res.voices.find((item) => item.id === initialVoiceId)?.id || res.voices[0]?.id || "",
        );
      })
      .catch((error) => {
        if (!controller.signal.aborted) useAppStore.getState().pushToast(errMsg(error), "error");
      })
      .finally(() => {
        if (!controller.signal.aborted) setVoicesLoading(false);
      });
    return () => controller.abort();
  }, [initialVoiceId, open, projectName, strategy]);

  const openModal = () => {
    if (disabled) return;
    const initialStrategy = videoConfigured ? "video" : "tts";
    if (initialStrategy === "tts") setVoicesLoading(true);
    setStrategy(initialStrategy);
    setText(t("voice_sample_text_default", { name: characterName }));
    setSelectedVoice("");
    setTaskId(null);
    setLastSeenTask(null);
    setIsPreviewPlaying(false);
    setOpen(true);
  };

  const close = () => {
    if (generating || confirming) return;
    setOpen(false);
    setIsPreviewPlaying(false);
  };

  const invalidateStalePreview = () => {
    if (!succeeded) return;
    setTaskId(null);
    setLastSeenTask(null);
    setIsPreviewPlaying(false);
  };

  const selectStrategy = (next: Strategy) => {
    if (generating || confirming) return;
    if (next === "tts") setVoicesLoading(true);
    setStrategy(next);
    invalidateStalePreview();
  };

  const handleGenerate = async () => {
    const trimmed = text.trim();
    const modeAvailable = strategy === "video" ? videoConfigured : audioConfigured && voicesConfigured;
    if (!trimmed || !modeAvailable || (strategy === "tts" && !selectedVoice) || generating || confirming) return;
    if (isResourceBusy("voice_sample", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setLocalSubmitting(true);
    setTaskId(null);
    setLastSeenTask(null);
    setIsPreviewPlaying(false);
    try {
      const response = await enqueueCharacterVoiceSample(projectName, characterName, {
        strategy,
        text: trimmed,
        ...(strategy === "tts" ? { voice: selectedVoice } : {}),
      });
      setTaskId(response.taskIds[0] ?? null);
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setLocalSubmitting(false);
    }
  };

  const handleConfirm = async () => {
    if (!taskId || !succeeded || confirming) return;
    setConfirming(true);
    try {
      await API.confirmCharacterVoiceSample(projectName, characterName, taskId);
      useAppStore.getState().pushToast(t("voice_sample_confirm_success_toast"), "success");
      setOpen(false);
      setTaskId(null);
      setLastSeenTask(null);
      await onSaved();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setConfirming(false);
    }
  };

  const unavailableHint = videoConfigured
    ? t("voice_sample_action")
    : audioConfigured
      ? t("voice_sample_video_unavailable_tts_hint")
      : t("voice_sample_not_configured_hint");

  return (
    <>
      <button
        type="button"
        onClick={openModal}
        disabled={disabled}
        title={unavailableHint}
        aria-label={unavailableHint}
        className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
        style={{ color: "var(--color-text-3)" }}
      >
        <AudioLines className="h-3.5 w-3.5" aria-hidden="true" />
      </button>

      <GlassModal
        open={open}
        onClose={close}
        labelledBy={titleId}
        describedBy={descId}
        closeOnBackdrop={!generating && !confirming}
        closeOnEscape={!generating && !confirming}
      >
        <div className="p-5">
          <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight" style={{ color: "var(--color-text)" }}>
            {t("voice_sample_modal_title")}
          </h2>
          <p id={descId} className="mt-1.5 text-[12.5px] leading-[1.55]" style={{ color: "var(--color-text-3)" }}>
            {t("voice_sample_modal_desc", { name: characterName })}
          </p>

          <div className="mt-4 grid grid-cols-2 gap-1 rounded-lg p-1" style={{ background: "oklch(0.20 0.011 265 / 0.6)" }}>
            {(["video", "tts"] as const).map((mode) => {
              const available = mode === "video" ? videoConfigured : audioConfigured;
              return (
                <button
                  key={mode}
                  type="button"
                  disabled={!available || generating || confirming}
                  onClick={() => selectStrategy(mode)}
                  className="focus-ring rounded-md px-2 py-1.5 text-[12px] font-medium disabled:cursor-not-allowed disabled:opacity-40"
                  style={{
                    color: strategy === mode ? "var(--color-text)" : "var(--color-text-3)",
                    background: strategy === mode ? "oklch(1 0 0 / 0.08)" : "transparent",
                  }}
                >
                  {t(mode === "video" ? "voice_sample_mode_video" : "voice_sample_mode_tts")}
                </button>
              );
            })}
          </div>

          {strategy === "video" ? (
            <p className="mt-2 rounded-lg px-3 py-2 text-[11.5px] leading-relaxed" style={{ background: "oklch(0.22 0.012 265 / 0.5)", color: "var(--color-text-3)" }}>
              {t("voice_sample_video_mode_hint")}
            </p>
          ) : !voicesLoading && !voicesConfigured ? (
            <p className="mt-2 rounded-lg px-3 py-2 text-[11.5px]" style={{ background: "oklch(0.22 0.012 265 / 0.5)", color: "var(--color-text-3)" }}>
              {t("voice_sample_not_configured_hint")}
            </p>
          ) : (
            <>
              <label htmlFor={voiceFieldId} className="mt-4 block text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--color-text-4)" }}>
                {t("voice_sample_voice_label")}
              </label>
              <select
                id={voiceFieldId}
                value={selectedVoice}
                onChange={(event) => {
                  setSelectedVoice(event.target.value);
                  invalidateStalePreview();
                }}
                disabled={voicesLoading || voices.length === 0 || generating || confirming}
                className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none disabled:cursor-not-allowed disabled:opacity-60"
                style={{ background: "oklch(0.20 0.011 265 / 0.6)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }}
              >
                {voicesLoading ? <option value="">{t("voice_sample_voice_loading")}</option> : voices.length === 0 ? <option value="">{t("voice_sample_no_voices")}</option> : voices.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </>
          )}

          <label htmlFor={textFieldId} className="mt-4 block text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--color-text-4)" }}>
            {t("voice_sample_text_label")}
          </label>
          <textarea
            id={textFieldId}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              invalidateStalePreview();
            }}
            maxLength={VOICE_SAMPLE_TEXT_MAX_LENGTH}
            rows={3}
            disabled={generating || confirming}
            className="focus-ring mt-1.5 w-full resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none disabled:cursor-not-allowed disabled:opacity-60"
            style={{ background: "oklch(0.20 0.011 265 / 0.6)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }}
          />
          <p className="mt-1 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{t("voice_sample_text_hint")}</p>

          {failed && <p className="mt-3 text-[12px]" style={{ color: "var(--color-danger, #e5484d)" }}>{task?.error_message ?? t("voice_sample_task_failed")}</p>}
          {previewUrl && (
            <div className="mt-3 flex items-center gap-2 rounded-lg px-2.5 py-1.5" style={{ background: "oklch(0.20 0.011 265 / 0.6)", border: "1px solid var(--color-hairline)" }}>
              {/* eslint-disable-next-line jsx-a11y/media-has-caption -- voice preview has no caption track */}
              <audio ref={previewAudioRef} src={previewUrl} className="hidden" onPlay={() => setIsPreviewPlaying(true)} onPause={() => setIsPreviewPlaying(false)} onEnded={() => setIsPreviewPlaying(false)} />
              <button
                type="button"
                onClick={() => {
                  const element = previewAudioRef.current;
                  if (!element) return;
                  if (isPreviewPlaying) element.pause();
                  else void element.play();
                }}
                aria-label={isPreviewPlaying ? t("pause_audio_sample") : t("play_audio_sample")}
                className="focus-ring grid h-7 w-7 shrink-0 place-items-center rounded-full"
                style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)", color: "var(--color-accent-2)" }}
              >
                {isPreviewPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 translate-x-px" />}
              </button>
              <span className="text-[11px]" style={{ color: "var(--color-text-3)" }}>{t("voice_sample_preview_label")}</span>
            </div>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <button type="button" onClick={close} disabled={generating || confirming} className="focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium disabled:cursor-not-allowed disabled:opacity-50" style={{ color: "var(--color-text-2)" }}>
              {t("common:cancel")}
            </button>
            {succeeded && (
              <button type="button" onClick={() => void handleConfirm()} disabled={confirming} className="focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium disabled:opacity-50" style={{ color: "var(--color-text)", background: "oklch(1 0 0 / 0.08)", border: "1px solid var(--color-hairline)" }}>
                {confirming ? t("voice_sample_confirming") : t("voice_sample_confirm")}
              </button>
            )}
            <button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={generating || confirming || text.trim().length === 0 || (strategy === "video" ? !videoConfigured : !voicesConfigured || !selectedVoice)}
              className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
              style={{ color: "oklch(0.14 0 0)", background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))" }}
            >
              <AudioLines className="h-3.5 w-3.5" aria-hidden="true" />
              {generating ? t("voice_sample_generating") : succeeded || failed ? t("voice_sample_regenerate") : t("voice_sample_generate")}
            </button>
          </div>
        </div>
      </GlassModal>
    </>
  );
}
