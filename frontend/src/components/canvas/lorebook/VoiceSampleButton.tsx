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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";
import { isAssetBusy, useAssetBusyNames } from "./assetBusyGuard";
import { useTrackWrite } from "./useAssetWrites";
import { errMsg } from "@/utils/async";

/** Sample-text length limit, matching backend VOICE_SAMPLE_TEXT_MAX_LENGTH.
 *  References require 2-10 seconds; character-to-duration ratios vary by language/voice, preventing exact pre-submit decisions.
 *  This coarse guard only prevents pasting whole passages; actual duration checks occur after execution persists files. Three-language defaults are about 120 characters;
 *  limit 200 leaves editing room. */
const VOICE_SAMPLE_TEXT_MAX_LENGTH = 200;

interface VoiceOption {
  id: string;
  label: string;
}

interface VoiceSampleButtonProps {
  projectName: string;
  characterName: string;
  /** Other detail generation/writes occupy the asset; disable entry. */
  busy?: boolean;
  /** Called after confirmed saving succeeds so callers reload character assets. */
  onSaved: () => Promise<unknown> | void;
}

/**
 * Character voice TTS sample: choose voice → edit defaults → generate → listen → confirm asset persistence.
 * Cancel/close never persists assets; only confirm-and-save promotes generated samples to character reference_audio.
 */
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

  // Audio-provider availability uses global config-status-store fetched at routing startup, without per-character
  // voice-list prefetches. Unconfigured entry is disabled, matching audio_backend-required availability.
  const audioConfigured = useConfigStatusStore((s) => s.availableMediaTypes.includes("audio"));

  const liveTask = useTasksStore((s) => (taskId ? s.tasks.find((item) => item.task_id === taskId) : undefined));

  // tasks-store retains only latest TASKS_PAGE_SIZE(200) rows; newer tasks may evict rows before terminal states.
  // Conservatively preserve occupancy both when rows have not yet arrived (generating must stay occupied) and when
  // observed queued/running rows are evicted. They likely still run server-side but are invisible to the client;
  // allowing close/edit/regenerate would lose tracking for still-billing tasks or falsely deduplicate new
  // submissions into invisible tasks. Both states stay occupied; only after explicitly observing
  // succeeded/failed terminal states may later disappearance release locking. Cache the latest observed taskId row,
  // not a boolean, so decisions use concrete states rather than whether rows ever appeared.
  const [lastSeenTask, setLastSeenTask] = useState<TaskItem | null>(null);
  useEffect(() => {
    if (taskId == null) return;
    const captureIfPresent = (tasks: TaskItem[]) => {
      const found = tasks.find((item) => item.task_id === taskId);
      if (found) setLastSeenTask(found);
    };
    // Subscription setState responds to external stores, not synchronous effect-body updates. Initial mounting may
    // find existing rows; defer initial lookup to microtasks, avoiding nested dispatch during subscription establishment.
    const unsubscribe = useTasksStore.subscribe((state) => captureIfPresent(state.tasks));
    void Promise.resolve().then(() => captureIfPresent(useTasksStore.getState().tasks));
    return unsubscribe;
  }, [taskId]);
  const task = liveTask ?? (lastSeenTask?.task_id === taskId ? lastSeenTask : undefined);

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
        // Also reject aborts occurring after responses resolve, preventing stale results entering component state.
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
    // Recheck occupancy on opening; Agents/other-tab tasks may occupy characters after render snapshots.
    if (busy || isAssetBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setText(t("voice_sample_text_default"));
    setSelectedVoice("");
    setTaskId(null);
    // Reset playback state: previous samples' <audio> unmount when taskId clears,
    // but isPreviewPlaying is independent and does not reset automatically. Without reset, newly mounted unplayed
    // <audio> would show pause icons due to stale true state.
    setIsPreviewPlaying(false);
    // Set synchronously in the dialog-open handler, not effects; the next effect fetches voices accordingly.
    setVoicesLoading(true);
    setOpen(true);
  };

  const close = () => {
    if (generating || confirming) return;
    setOpen(false);
  };

  // After generation succeeds, changing voice/text without regenerating would leave Confirm visible
  // and pointing to old samples: users see new input but save bytes synthesized from old voice/text. Clear taskId only
  // on succeeded, hiding preview/confirmation and requiring regeneration. Never clear mid-generation,
  // or tracking for still-synthesizing, already-billed tasks is lost.
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
    if (!trimmed || !selectedVoice || generating || confirming) return;
    // Occupancy may change while open; recheck latest store state before submission (docs/standards/frontend.md).
    if (busy || isAssetBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setLocalSubmitting(true);
    setTaskId(null);
    // Clearing taskId immediately unmounts the previous preview <audio>, but independent isPreviewPlaying state
    // does not automatically reset. Otherwise newly mounted unplayed <audio> shows a pause icon from stale
    // true; clicking pause() on already-paused elements emits no new event,
    // permanently trapping playback state before it can start.
    setIsPreviewPlaying(false);
    try {
      const res = await enqueueCharacterVoiceSample(projectName, characterName, trimmed, selectedVoice);
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

  const selectItems = voicesLoading
    ? [{ value: null, label: t("voice_sample_voice_loading") }]
    : voices.length === 0
      ? [{ value: null, label: t("voice_sample_no_voices") }]
      : [{ value: null, label: t("voice_sample_voice_placeholder") }, ...voices.map((v) => ({ value: v.id, label: v.label }))];
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
                  <Select
                    items={selectItems}
                    value={selectedVoice || null}
                    onValueChange={(next) => {
                      if (next !== null) handleVoiceChange(next);
                    }}
                    disabled={voicesLoading || voices.length === 0 || locked}
                  >
                    <SelectTrigger id={voiceFieldId} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {voices.map((v) => (
                        <SelectItem key={v.id} value={v.id}>
                          {v.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
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
                    {/* eslint-disable-next-line jsx-a11y/media-has-caption -- TTS preview sample without dialogue content or subtitle sources */}
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
              disabled={busy || occupied || locked || !voicesConfigured || !selectedVoice || text.trim().length === 0}
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
