import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic, Pause, Play } from "lucide-react";
import { enqueueCharacterVoiceSample } from "@/actions/generation";
import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { isResourceBusy, useTasksStore } from "@/stores/tasks-store";
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
  /** 角色卡上其它生成/编辑任务占用中：禁用入口 */
  busy?: boolean;
  /** 确认保存成功后触发，供调用方重新加载角色资产 */
  onSaved: () => Promise<unknown> | void;
}

/**
 * 角色卡「声音」分组的 TTS 试听样本入口：选音色 → 编辑默认文案 → 生成 → 试听 → 确认落资产。
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
  const titleId = useId();
  const descId = useId();
  const voiceFieldId = useId();
  const textFieldId = useId();

  // audio 供应商可用性走全局 config-status-store（路由启动时已拉取一次），不为每张角色卡
  // 预取音色列表——入口在未配置时即禁用，符合「audio_backend 未配置该入口不可用」的口径。
  const audioConfigured = useConfigStatusStore((s) => s.availableMediaTypes.includes("audio"));

  const task = useTasksStore((s) => (taskId ? s.tasks.find((item) => item.task_id === taskId) : undefined));
  // taskId 落定（enqueue 请求已成功）到该任务行出现在 tasks-store 快照之间有轮询间隔的
  // 空窗——这段时间 localSubmitting 已经复位，但 task 在 store 里还查不到（非 undefined
  // 判空会误判为「未在生成」）。taskId 非空且尚未拿到该行时按仍在生成处理，避免这段空窗
  // 被误判为空闲从而允许关闭弹窗，导致丢失一个仍在合成、且已经计费的任务的追踪入口。
  const generating =
    localSubmitting || (taskId != null && (task == null || task.status === "queued" || task.status === "running"));
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
    // 弹窗打开期间 busy 态可能已变化，提交前用 store 新鲜读复核（frontend-ui-conventions 纪律）。
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
      useAppStore.getState().pushToast(t("voice_sample_confirm_success_toast"), "success");
      setOpen(false);
      setTaskId(null);
      await onSaved();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={openModal}
        disabled={disabled}
        title={audioConfigured ? t("voice_sample_action") : t("voice_sample_not_configured_hint")}
        aria-label={audioConfigured ? t("voice_sample_action") : t("voice_sample_not_configured_hint")}
        className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
        style={{ color: "var(--color-text-3)" }}
      >
        <Mic className="h-3 w-3" aria-hidden="true" />
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
          <h2
            id={titleId}
            className="display-serif text-[17px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("voice_sample_modal_title")}
          </h2>
          <p id={descId} className="mt-1.5 text-[12.5px] leading-[1.55]" style={{ color: "var(--color-text-3)" }}>
            {t("voice_sample_modal_desc", { name: characterName })}
          </p>

          {!voicesLoading && !voicesConfigured ? (
            <p
              className="mt-4 rounded-lg px-3 py-2 text-[12.5px]"
              style={{ background: "oklch(0.22 0.012 265 / 0.5)", color: "var(--color-text-3)" }}
            >
              {t("voice_sample_not_configured_hint")}
            </p>
          ) : (
            <>
              <label
                htmlFor={voiceFieldId}
                className="mt-4 block text-[10px] font-semibold uppercase tracking-[0.12em]"
                style={{ color: "var(--color-text-4)" }}
              >
                {t("voice_sample_voice_label")}
              </label>
              <select
                id={voiceFieldId}
                value={selectedVoice}
                onChange={(e) => handleVoiceChange(e.target.value)}
                disabled={voicesLoading || voices.length === 0}
                className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none transition-[border-color,box-shadow] disabled:cursor-not-allowed disabled:opacity-60"
                style={{
                  background: "oklch(0.20 0.011 265 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                }}
              >
                {voicesLoading ? (
                  <option value="">{t("voice_sample_voice_loading")}</option>
                ) : voices.length === 0 ? (
                  <option value="">{t("voice_sample_no_voices")}</option>
                ) : (
                  <>
                    <option value="" disabled>
                      {t("voice_sample_voice_placeholder")}
                    </option>
                    {voices.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.label}
                      </option>
                    ))}
                  </>
                )}
              </select>

              <label
                htmlFor={textFieldId}
                className="mt-4 block text-[10px] font-semibold uppercase tracking-[0.12em]"
                style={{ color: "var(--color-text-4)" }}
              >
                {t("voice_sample_text_label")}
              </label>
              <textarea
                id={textFieldId}
                value={text}
                onChange={(e) => handleTextChange(e.target.value)}
                maxLength={VOICE_SAMPLE_TEXT_MAX_LENGTH}
                rows={3}
                className="focus-ring mt-1.5 w-full resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
                style={{
                  background: "oklch(0.20 0.011 265 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                }}
              />
              <p className="mt-1 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
                {t("voice_sample_text_hint")}
              </p>

              {failed && (
                <p className="mt-3 text-[12px]" style={{ color: "var(--color-danger, #e5484d)" }}>
                  {task?.error_message ?? t("voice_sample_task_failed")}
                </p>
              )}

              {previewUrl && (
                <div
                  className="mt-3 flex items-center gap-2 rounded-lg px-2.5 py-1.5"
                  style={{
                    background: "oklch(0.20 0.011 265 / 0.6)",
                    border: "1px solid var(--color-hairline)",
                  }}
                >
                  {/* eslint-disable-next-line jsx-a11y/media-has-caption -- TTS 试听样本，无对白内容，无字幕源 */}
                  <audio
                    ref={previewAudioRef}
                    src={previewUrl}
                    className="hidden"
                    onPlay={() => setIsPreviewPlaying(true)}
                    onPause={() => setIsPreviewPlaying(false)}
                    onEnded={() => setIsPreviewPlaying(false)}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const audioEl = previewAudioRef.current;
                      if (!audioEl) return;
                      if (isPreviewPlaying) audioEl.pause();
                      else void audioEl.play();
                    }}
                    aria-label={isPreviewPlaying ? t("pause_audio_sample") : t("play_audio_sample")}
                    className="focus-ring grid h-7 w-7 shrink-0 place-items-center rounded-full transition-colors"
                    style={{
                      background: "var(--color-accent-dim)",
                      border: "1px solid var(--color-accent-soft)",
                      color: "var(--color-accent-2)",
                    }}
                  >
                    {isPreviewPlaying ? (
                      <Pause className="h-3.5 w-3.5" />
                    ) : (
                      <Play className="h-3.5 w-3.5 translate-x-px" />
                    )}
                  </button>
                  <span className="text-[11px]" style={{ color: "var(--color-text-3)" }}>
                    {t("voice_sample_preview_label")}
                  </span>
                </div>
              )}
            </>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={close}
              disabled={generating || confirming}
              className="focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-50"
              style={{ color: "var(--color-text-2)" }}
            >
              {t("common:cancel")}
            </button>
            {succeeded && (
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={confirming}
                className="focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                style={{
                  color: "var(--color-text)",
                  background: "oklch(1 0 0 / 0.08)",
                  border: "1px solid var(--color-hairline)",
                }}
              >
                {confirming ? t("voice_sample_confirming") : t("voice_sample_confirm")}
              </button>
            )}
            <button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={
                generating ||
                confirming ||
                !voicesConfigured ||
                !selectedVoice ||
                text.trim().length === 0
              }
              className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                color: "oklch(0.14 0 0)",
                background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
              }}
            >
              <Mic className="h-3.5 w-3.5" aria-hidden="true" />
              {generating
                ? t("voice_sample_generating")
                : succeeded || failed
                  ? t("voice_sample_regenerate")
                  : t("voice_sample_generate")}
            </button>
          </div>
        </div>
      </GlassModal>
    </>
  );
}
