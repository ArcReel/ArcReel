import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Mic, Pause, Play } from "lucide-react";
import { enqueueCharacterVoiceSample } from "@/actions/generation";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NarrationVoiceInput } from "@/components/shared/NarrationVoiceInput";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useTasksStore } from "@/stores/tasks-store";
import { isTerminalStatus, type TaskItem } from "@/types";
import { isAssetBusy, useAssetBusyNames } from "./assetBusyGuard";
import { useTrackWrite } from "./useAssetWrites";
import { errMsg } from "@/utils/async";

const VOICE_SAMPLE_TEXT_MAX_LENGTH = 200;
const VOICE_SAMPLE_TASK_POLL_MS = 2000;
const VOICE_SAMPLE_LOOKUP_TIMEOUT_MS = 10_000;

async function lookupVoiceSampleTask(taskId: string, signal: AbortSignal): Promise<TaskItem | null> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const aborted = new Promise<null>((resolve) => {
    controller.signal.addEventListener("abort", () => resolve(null), { once: true });
  });
  const deadline = setTimeout(abort, VOICE_SAMPLE_LOOKUP_TIMEOUT_MS);
  try {
    const task = await Promise.race([API.getTask(taskId, { signal: controller.signal }).catch(() => null), aborted]);
    return controller.signal.aborted ? null : task;
  } finally {
    clearTimeout(deadline);
    signal.removeEventListener("abort", abort);
  }
}

interface VoiceOption {
  id: string;
  label: string;
}

interface VoiceSampleButtonProps {
  projectName: string;
  characterName: string;
  busy?: boolean;
  onSaved: () => Promise<unknown> | void;
}

export function VoiceSampleButton({
  projectName,
  characterName,
  busy = false,
  onSaved,
}: VoiceSampleButtonProps) {
  const { t } = useTranslation("dashboard");
  const track = useTrackWrite();
  const occupied = useAssetBusyNames("character", projectName).has(characterName);
  const [open, setOpen] = useState(false);
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [voicesConfigured, setVoicesConfigured] = useState(true);
  const [voices, setVoices] = useState<VoiceOption[]>([]);
  const [selectedVoice, setSelectedVoice] = useState("");
  const [text, setText] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [localSubmitting, setLocalSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [isPreviewPlaying, setIsPreviewPlaying] = useState(false);
  const previewAudioRef = useRef<HTMLAudioElement>(null);
  const voiceFieldId = useId();
  const textFieldId = useId();

  const audioConfigured = useConfigStatusStore((s) => s.availableMediaTypes.includes("audio"));

  const liveTask = useTasksStore((s) => (taskId ? s.tasks.find((item) => item.task_id === taskId) : undefined));

  const [settledTask, setSettledTask] = useState<TaskItem | null>(null);
  useEffect(() => {
    if (!open || taskId === null) return;
    const controller = new AbortController();
    const captureTerminalTask = (tasks: TaskItem[]) => {
      if (controller.signal.aborted) return;
      const found = tasks.find((item) => item.task_id === taskId);
      if (found && isTerminalStatus(found.status)) setSettledTask(found);
    };
    const unsubscribe = useTasksStore.subscribe((state) => captureTerminalTask(state.tasks));
    void Promise.resolve().then(() => captureTerminalTask(useTasksStore.getState().tasks));
    return () => {
      controller.abort();
      unsubscribe();
    };
  }, [open, taskId]);
  const task = settledTask?.task_id === taskId ? settledTask : liveTask;

  useEffect(() => {
    if (!open || taskId === null || task !== undefined) return;
    const controller = new AbortController();
    let pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      const current = await lookupVoiceSampleTask(taskId, controller.signal);
      pending = false;
      if (controller.signal.aborted) return;
      if (current && isTerminalStatus(current.status)) {
        clearInterval(timer);
        setSettledTask(current);
        useTasksStore.getState().settleVoiceSample(current);
      }
    };
    const timer = setInterval(() => void poll(), VOICE_SAMPLE_TASK_POLL_MS);
    void poll();
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [open, taskId, task]);

  const generating =
    localSubmitting ||
    (taskId != null && (task == null || task.status === "queued" || task.status === "running"));
  const failed = task?.status === "failed";
  const succeeded = task?.status === "succeeded";
  const previewFilePath =
    succeeded && task?.result && typeof task.result.file_path === "string" ? task.result.file_path : null;
  const previewUrl = previewFilePath ? API.getFileUrl(projectName, previewFilePath, taskId) : null;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    API.getAudioBackendVoices(projectName, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setVoicesConfigured(res.configured);
        setVoices(res.voices);
        setSelectedVoice((prev) => prev || res.voices[0]?.id || "");
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        useAppStore.getState().pushToast(errMsg(err), "error");
      })
      .finally(() => {
        if (!controller.signal.aborted) setVoicesLoading(false);
      });
    return () => controller.abort();
  }, [open, projectName]);

  const disabled = busy || occupied || !audioConfigured;

  const openModal = () => {
    if (disabled) return;
    if (busy || isAssetBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setText(t("voice_sample_text_default"));
    setSelectedVoice("");
    setTaskId(null);
    setSettledTask(null);
    setIsPreviewPlaying(false);
    setVoicesLoading(true);
    setOpen(true);
  };

  const close = () => {
    if (generating || confirming) return;
    setOpen(false);
  };

  const invalidateStalePreview = () => {
    if (succeeded) {
      setTaskId(null);
      setIsPreviewPlaying(false);
    }
  };

  const handleVoiceChange = (voiceId: string) => {
    setSelectedVoice(voiceId);
    invalidateStalePreview();
  };

  const handleTextChange = (value: string) => {
    setText(value);
    invalidateStalePreview();
  };

  const handleGenerate = async () => {
    const trimmed = text.trim();
    const voice = selectedVoice.trim();
    if (!trimmed || !voice || generating || confirming) return;
    if (busy || isAssetBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setLocalSubmitting(true);
    setTaskId(null);
    setSettledTask(null);
    setIsPreviewPlaying(false);
    try {
      const res = await enqueueCharacterVoiceSample(projectName, characterName, trimmed, voice);
      setTaskId(res.taskIds[0] ?? null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setLocalSubmitting(false);
    }
  };

  const handleConfirm = async () => {
    if (!taskId || !succeeded || confirming) return;
    if (busy || isAssetBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setConfirming(true);
    try {
      await track(API.confirmCharacterVoiceSample(projectName, characterName, taskId).then(() => onSaved()));
      setOpen(false);
      setTaskId(null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setConfirming(false);
    }
  };

  const voiceHint = voicesLoading
    ? t("voice_sample_voice_loading")
    : voices.length === 0
      ? t("voice_sample_no_voices")
      : t("voice_sample_voice_hint");
  const locked = generating || confirming;

  return (
    <>
      <span className="flex flex-col items-start gap-1">
        <Button variant="outline" size="sm" onClick={openModal} disabled={disabled}>
          <Mic aria-hidden data-icon="inline-start" />
          {t("voice_sample_action")}
        </Button>
        {audioConfigured ? null : (
          <span className="text-xs text-muted-foreground">{t("voice_sample_not_configured_hint")}</span>
        )}
      </span>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) close();
        }}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{t("voice_sample_modal_title")}</DialogTitle>
            <DialogDescription>{t("voice_sample_modal_desc", { name: characterName })}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            {!voicesLoading && !voicesConfigured ? (
              <p className="text-sm text-muted-foreground">{t("voice_sample_not_configured_hint")}</p>
            ) : (
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={voiceFieldId}>{t("voice_sample_voice_label")}</Label>
                  <NarrationVoiceInput
                    id={voiceFieldId}
                    value={selectedVoice}
                    onChange={handleVoiceChange}
                    voices={voices}
                    disabled={voicesLoading || locked}
                    aria-describedby={`${voiceFieldId}-hint`}
                  />
                  <p id={`${voiceFieldId}-hint`} className="text-xs text-muted-foreground">
                    {voiceHint}
                  </p>
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={textFieldId}>{t("voice_sample_text_label")}</Label>
                  <Textarea
                    id={textFieldId}
                    value={text}
                    onChange={(e) => handleTextChange(e.target.value)}
                    maxLength={VOICE_SAMPLE_TEXT_MAX_LENGTH}
                    rows={3}
                    disabled={locked}
                  />
                  <p className="text-xs text-muted-foreground">{t("voice_sample_text_hint")}</p>
                </div>

                {failed && (
                  <p role="alert" className="text-sm text-destructive">
                    {task?.error_message ?? t("voice_sample_task_failed")}
                  </p>
                )}

                {previewUrl && (
                  <div className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5">
                    {/* eslint-disable-next-line jsx-a11y/media-has-caption -- TTS 试听样本，无对白内容，无字幕源 */}
                    <audio
                      ref={previewAudioRef}
                      src={previewUrl}
                      className="hidden"
                      onPlay={() => setIsPreviewPlaying(true)}
                      onPause={() => setIsPreviewPlaying(false)}
                      onEnded={() => setIsPreviewPlaying(false)}
                    />
                    <Button
                      variant="outline"
                      size="icon-sm"
                      onClick={() => {
                        const audioEl = previewAudioRef.current;
                        if (!audioEl) return;
                        if (isPreviewPlaying) audioEl.pause();
                        else void audioEl.play();
                      }}
                      aria-label={isPreviewPlaying ? t("pause_audio_sample") : t("play_audio_sample")}
                    >
                      {isPreviewPlaying ? <Pause aria-hidden /> : <Play aria-hidden />}
                    </Button>
                    <span className="text-sm text-muted-foreground">{t("voice_sample_preview_label")}</span>
                  </div>
                )}
              </div>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={locked}>
              {t("common:cancel")}
            </Button>
            <Button
              variant={succeeded ? "outline" : "default"}
              onClick={() => void handleGenerate()}
              disabled={busy || occupied || locked || !voicesConfigured || !selectedVoice.trim() || text.trim().length === 0}
            >
              {generating ? (
                <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
              ) : (
                <Mic aria-hidden data-icon="inline-start" />
              )}
              {generating
                ? t("voice_sample_generating")
                : succeeded || failed
                  ? t("voice_sample_regenerate")
                  : t("voice_sample_generate")}
            </Button>
            {succeeded && (
              <Button onClick={() => void handleConfirm()} disabled={confirming || busy || occupied}>
                {confirming ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                {confirming ? t("voice_sample_confirming") : t("voice_sample_confirm")}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
