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
import { isResourceBusy, useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";
import { errMsg } from "@/utils/async";

/** 样本文案长度上限（与后端 VOICE_SAMPLE_TEXT_MAX_LENGTH 同值）。
 *  参考音频要求 2-10 秒，而「多少字合成出几秒」随语种与音色而变，无法在提交前精确判定；
 *  此处只作粗护栏挡住整段粘贴——真正的时长判定仍在执行层落盘后做。三语默认文案约 120 字符，
 *  上限取 200 以留出编辑余量。 */
const VOICE_SAMPLE_TEXT_MAX_LENGTH = 200;

interface VoiceOption {
  id: string;
  label: string;
}

interface VoiceSampleButtonProps {
  projectName: string;
  characterName: string;
  /** 详情编辑器里其它生成/写入占用中：禁用入口 */
  busy?: boolean;
  /** 确认保存成功后触发，供调用方重新加载角色资产 */
  onSaved: () => Promise<unknown> | void;
}

/**
 * 角色详情「声音」区块的 TTS 试听样本入口：选音色 → 编辑默认文案 → 生成 → 试听 → 确认落资产。
 * 取消/关闭弹窗不落资产——只有点击「确认并保存」才把已生成样本提升为角色 reference_audio。
 */
export function VoiceSampleButton({
  projectName,
  characterName,
  busy = false,
  onSaved,
}: VoiceSampleButtonProps) {
  const { t } = useTranslation("dashboard");
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

  // audio 供应商可用性走全局 config-status-store（路由启动时已拉取一次），不为每个角色
  // 预取音色列表——入口在未配置时即禁用，符合「audio_backend 未配置该入口不可用」的口径。
  const audioConfigured = useConfigStatusStore((s) => s.availableMediaTypes.includes("audio"));

  const liveTask = useTasksStore((s) => (taskId ? s.tasks.find((item) => item.task_id === taskId) : undefined));

  // tasks-store 只保留最新 TASKS_PAGE_SIZE(200) 行快照：任务行可能在到达终态之前就被更多
  // 新任务挤出快照。把「尚未落地」（generating 该继续按占用处理）与「落地过、还在
  // running/queued 时被挤出」（同样该继续按占用处理——它很可能仍在服务端跑，只是客户端看
  // 不到了；一旦在这个状态下放行关闭/编辑/重新生成，会丢失一个仍在计费任务的追踪，或让
  // 新提交被 dedupe 误合并进这个隐形任务）两者都保守地当作占用中；只有明确观察到过
  // succeeded/failed 终态后再消失，才不再继续锁定。缓存「最近一次观察到的该 taskId 任务行」
  // 而非单纯的布尔标记，让这个判断能落到具体状态而不是「有没有出现过」。
  const [lastSeenTask, setLastSeenTask] = useState<TaskItem | null>(null);
  useEffect(() => {
    if (taskId == null) return;
    const captureIfPresent = (tasks: TaskItem[]) => {
      const found = tasks.find((item) => item.task_id === taskId);
      if (found) setLastSeenTask(found);
    };
    // 订阅回调里 setState 是响应外部 store 变化，不是效应体内的同步 setState；首次挂载时
    // store 里可能已经有这一行，延后到微任务里查一次初始值，避免在订阅建立期间嵌套 dispatch。
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
        // 响应已 resolve 之后才发生的 abort 也要拦住，别把过期结果写进组件状态。
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

  const disabled = busy || !audioConfigured;

  const openModal = () => {
    if (disabled) return;
    // 打开时复核占用态：渲染快照之外，角色可能刚被 Agent 或其他标签页的任务占用。
    if (isResourceBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setText(t("voice_sample_text_default"));
    setSelectedVoice("");
    setTaskId(null);
    // 复位播放态：上一次打开期间播放过的样本，其 <audio> 元素随 taskId 清空已卸载，
    // 但 isPreviewPlaying 独立于 taskId,不会跟着卸载自动复位——不重置的话，本次新样本
    // 挂载的全新（未播放）<audio> 元素会因这个陈旧的 true 值渲染成「暂停」图标。
    setIsPreviewPlaying(false);
    // 弹窗打开这一事件处理器内同步置位（非 effect 内），紧随其后的 effect 据此拉取音色列表。
    setVoicesLoading(true);
    setOpen(true);
  };

  const close = () => {
    if (generating || confirming) return;
    setOpen(false);
  };

  // 样本已生成成功后，若用户改选音色或改动文案却没有点「重新生成」，Confirm 按钮仍会显示
  // 且仍指向旧样本——用户看到的是新输入，确认保存的却是旧音色/旧文案合成的字节。只在
  // succeeded 时清空 taskId 让预览/确认区一并隐藏，逼用户重新生成；不在生成中途清空，
  // 否则会丢失一个仍在合成、且已计费任务的追踪入口。
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
    // 弹窗打开期间占用态可能已变化，提交前从 store 读取最新占用态复核（见 docs/standards/frontend-ui.md）。
    if (isResourceBusy("character", projectName, characterName)) {
      useAppStore.getState().pushToast(t("voice_sample_resource_busy"), "error");
      return;
    }
    setLocalSubmitting(true);
    setTaskId(null);
    // 上一条预览的 <audio> 元素随 taskId 清空即刻卸载，但 isPreviewPlaying 是独立 state，
    // 不会跟着卸载自动复位——不重置的话，新样本挂载的全新（未播放）<audio> 元素会因这个
    // 陈旧的 true 值渲染成「暂停」图标，点击对一个已暂停的元素调 pause() 不产生新事件，
    // 播放态就此卡死，永远进不了播放。
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
    setConfirming(true);
    try {
      await API.confirmCharacterVoiceSample(projectName, characterName, taskId);
      setOpen(false);
      setTaskId(null);
      await onSaved();
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
              disabled={locked || !voicesConfigured || !selectedVoice || text.trim().length === 0}
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
              <Button onClick={() => void handleConfirm()} disabled={confirming}>
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
