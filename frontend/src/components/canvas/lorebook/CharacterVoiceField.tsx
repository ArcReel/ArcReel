import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, Loader2, Pause, Play, Trash2, Upload } from "lucide-react";
import { API } from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { CharacterVoiceBinding } from "@/types";
import { errMsg } from "@/utils/async";
import { buildEntityRevisionKey } from "@/utils/project-changes";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { useTrackWrite } from "./useAssetWrites";
import { VoiceSampleButton } from "./VoiceSampleButton";

const AUDIO_ACCEPT = ".wav,.mp3";

/** mm:ss；无法确定时长（尚未加载完 metadata）时占位 --:--。 */
function formatAudioDuration(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** 参考音频的试听条。换音源时由调用方以 key 重建，播放进度不残留。 */
function AudioPlayer({ src }: { src: string }) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);

  return (
    <>
      <Button
        variant="outline"
        size="icon-sm"
        onClick={() => {
          const el = audioRef.current;
          if (!el) return;
          if (playing) el.pause();
          else void el.play();
        }}
        aria-label={playing ? t("dashboard:pause_audio_sample") : t("dashboard:play_audio_sample")}
      >
        {playing ? <Pause aria-hidden /> : <Play aria-hidden />}
      </Button>
      <Progress value={Math.round(progress * 100)} aria-label={t("assets:reference_audio")} className="min-w-0 flex-1" />
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{formatAudioDuration(duration)}</span>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 角色参考音频样本，无对白内容，无字幕源 */}
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        className="hidden"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onTimeUpdate={(e) => {
          const el = e.currentTarget;
          setProgress(el.duration ? el.currentTime / el.duration : 0);
        }}
        onEnded={(e) => {
          e.currentTarget.currentTime = 0;
          setPlaying(false);
          setProgress(0);
        }}
      />
    </>
  );
}

interface CharacterVoiceFieldProps {
  projectName: string;
  name: string;
  /** 声音风格属于编辑单元的字段，由调用方持有。 */
  voiceStyle: string;
  onVoiceStyleChange: (value: string) => void;
  referenceAudio: string | undefined;
  voiceBinding: CharacterVoiceBinding;
  readOnly?: boolean;
  /** 详情里其它写入或生成占用中：上传、删除与生成样本一起禁用。 */
  busy?: boolean;
}

/**
 * 角色的「声音」：声音风格进入未保存修改；参考音频的上传、删除与 TTS 生成样本立即执行。
 * 项目用提示词约束角色声音时参考音频不生效，折叠起来并说明怎样才生效。
 */
export function CharacterVoiceField({
  projectName,
  name,
  voiceStyle,
  onVoiceStyleChange,
  referenceAudio,
  voiceBinding,
  readOnly = false,
  busy = false,
}: CharacterVoiceFieldProps) {
  const { t } = useTranslation(["dashboard", "assets", "common"]);
  const voiceStyleId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const track = useTrackWrite();
  const audioFp = useProjectsStore((s) => (referenceAudio ? s.getAssetFingerprint(referenceAudio) : null));
  // 同扩展名替换（如 wav 换 wav）路径不变，靠指纹才能感知内容已更新
  const audioUrl = referenceAudio ? API.getFileUrl(projectName, referenceAudio, audioFp) : null;
  const disabled = busy || uploading || deleting;

  const refresh = () =>
    track(refreshAfterWrite(projectName, t, { invalidateKeys: [buildEntityRevisionKey("character", name)] }));

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || disabled) return;
    if (rejectIfAssetBusy("character", projectName, name, t, "assets:gallery_busy_hint")) return;
    setUploading(true);
    try {
      await track(API.uploadFile(projectName, "character_audio_ref", file, name));
      await refresh();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploading(false);
    }
  };

  // 打开确认框与确认删除时都复核占用态
  const requestDelete = () => {
    if (disabled || rejectIfAssetBusy("character", projectName, name, t, "assets:delete_audio_busy_hint")) return;
    setConfirmingDelete(true);
  };

  const executeDelete = async () => {
    if (disabled) return;
    if (rejectIfAssetBusy("character", projectName, name, t, "assets:delete_audio_busy_hint")) {
      setConfirmingDelete(false);
      return;
    }
    setDeleting(true);
    try {
      await track(API.deleteCharacterReferenceAudio(projectName, name));
      setConfirmingDelete(false);
      await refresh();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeleting(false);
    }
  };

  const audio = (
    <div className="flex flex-col gap-1.5">
      {audioUrl ? (
        <div className="flex items-center gap-2 rounded-lg border border-border px-2 py-1.5">
          <AudioPlayer key={audioUrl} src={audioUrl} />
          {readOnly ? null : (
            <>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => inputRef.current?.click()}
                disabled={disabled}
                aria-label={t("assets:reference_audio_replace")}
              >
                {uploading ? <Loader2 aria-hidden className="animate-spin" /> : <Upload aria-hidden />}
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={requestDelete}
                disabled={disabled}
                aria-label={t("dashboard:delete_audio_sample")}
              >
                <Trash2 aria-hidden />
              </Button>
            </>
          )}
        </div>
      ) : readOnly ? (
        <p className="text-sm text-muted-foreground">{t("assets:reference_audio_none")}</p>
      ) : (
        <>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={disabled}
            className="focus-ring flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-border px-3 py-3 text-sm text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <Upload aria-hidden className="size-4" />}
            {t("dashboard:upload_reference_audio")}
          </button>
          <p className="text-xs text-muted-foreground">{t("dashboard:reference_audio_hint")}</p>
        </>
      )}
      {readOnly ? null : (
        <>
          <div>
            <VoiceSampleButton projectName={projectName} characterName={name} busy={disabled} onSaved={refresh} />
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={AUDIO_ACCEPT}
            aria-label={t("dashboard:upload_reference_audio")}
            onChange={(e) => void upload(e)}
            className="hidden"
          />
        </>
      )}
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={voiceStyleId}>{t("dashboard:voice_style")}</Label>
        <Input
          id={voiceStyleId}
          value={voiceStyle}
          readOnly={readOnly}
          onChange={(e) => onVoiceStyleChange(e.target.value)}
          placeholder={t("dashboard:voice_style_example")}
        />
      </div>
      {readOnly && !audioUrl ? null : voiceBinding === "prompt" ? (
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2" />}>
            {t("dashboard:character_voice_binding_optional_summary")}
            <ChevronRight aria-hidden data-icon="inline-end" className="transition-transform group-aria-expanded/button:rotate-90" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="flex flex-col gap-1.5 pt-1.5">
              <p className="text-xs text-muted-foreground">{t("dashboard:character_voice_binding_optional_hint")}</p>
              {audio}
            </div>
          </CollapsibleContent>
        </Collapsible>
      ) : (
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">{t("assets:reference_audio")}</span>
          {audio}
        </div>
      )}
      <AlertDialog
        open={confirmingDelete}
        onOpenChange={(next) => {
          if (!next && !deleting) setConfirmingDelete(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("assets:reference_audio_delete_title")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody>
            <AlertDialogDescription>{t("assets:reference_audio_delete_description", { name })}</AlertDialogDescription>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={disabled} onClick={() => void executeDelete()}>
              {deleting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {t("common:delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
