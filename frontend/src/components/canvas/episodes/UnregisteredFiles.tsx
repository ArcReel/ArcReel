import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, FileQuestion, Trash2 } from "lucide-react";

import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import type { EpisodeMeta, UnregisteredSourceFile } from "@/types";
import { errMsg } from "@/utils/async";
import { episodePosition } from "@/utils/episode-display";
import { formatNameList } from "@/utils/list-format";

import { ImpactConfirmDialog } from "./ImpactConfirmDialog";

/** 提示条里最多点名的文件数，其余以「等」概括。 */
const NAMED_FILES = 2;

interface UnregisteredFilesProps {
  projectName: string;
  files: UnregisteredSourceFile[];
  episodes: EpisodeMeta[];
  /** 处置完成后重新拉取「分集」视图。 */
  onChanged: () => void;
}

/**
 * 页头下方的提示条：source/ 里有不属于整本源文、也不是任何一集原文的文件。点「处理」打开对话框逐个处置：
 * 加入整本源文、作为某一集的原文（新的一集或一集无原文的集），或者删除。没有这类文件时不渲染。
 */
export function UnregisteredFilesBanner({ projectName, files, episodes, onChanged }: UnregisteredFilesProps) {
  const { t, i18n } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  if (files.length === 0) return null;

  const named = files.slice(0, NAMED_FILES).map((file) => file.name);
  return (
    <>
      <div className="shrink-0 px-4 pt-3">
        <Alert role="status">
          <FileQuestion aria-hidden className="text-warn" />
          <AlertDescription>
            {t(files.length > NAMED_FILES ? "unregistered_banner_more" : "unregistered_banner", {
              count: files.length,
              names: formatNameList(named, i18n.language),
            })}
          </AlertDescription>
          <AlertAction>
            <Button size="xs" variant="outline" onClick={() => setOpen(true)}>
              {t("unregistered_handle")}
            </Button>
          </AlertAction>
        </Alert>
      </div>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          // 处置请求在途时不响应 Esc 与遮罩点击
          if (!next && !busy) setOpen(false);
        }}
      >
        <DialogContent showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>{t("unregistered_dialog_title")}</DialogTitle>
            <DialogDescription>{t("unregistered_hint")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <UnregisteredFileList
              projectName={projectName}
              files={files}
              episodes={episodes}
              onChanged={onChanged}
              busy={busy}
              setBusy={setBusy}
            />
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UnregisteredFileList({
  projectName,
  files,
  episodes,
  onChanged,
  busy,
  setBusy,
}: UnregisteredFilesProps & { busy: boolean; setBusy: (busy: boolean) => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const noSourceEpisodes = episodes.filter((episode) => episode.source_origin === "none");

  /** 处置一个文件：成功后文件从列表里消失，失败时在列表上方说明原因。 */
  const run = async (name: string, action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setDeleting(null);
      await refreshAfterWrite(projectName, t);
      onChanged();
    } catch (err) {
      setDeleting(null);
      setError(t("dashboard:unregistered_action_failed", { name, message: errMsg(err) }));
    } finally {
      setBusy(false);
    }
  };

  const assign = (name: string, episode: number | null) =>
    void run(name, () => API.adoptSourceFile(projectName, name, { target: "episode", episode }));

  return (
    <div className="flex flex-col gap-3">
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {files.map((file) => (
          <li key={file.name} aria-label={file.name} className="flex flex-col gap-2 rounded-lg border px-3 py-2.5">
            <div className="flex min-w-0 items-center gap-2">
              <TruncatedText text={file.name} className="flex-1 text-sm text-foreground" />
              <Button
                variant="ghost"
                size="icon-sm"
                disabled={busy}
                onClick={() => setDeleting(file.name)}
                aria-label={t("dashboard:unregistered_delete_aria", { name: file.name })}
              >
                <Trash2 aria-hidden />
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="xs"
                disabled={busy || !file.can_join_whole_source}
                onClick={() =>
                  void run(file.name, () => API.adoptSourceFile(projectName, file.name, { target: "whole_source" }))
                }
              >
                {t("dashboard:unregistered_join_whole")}
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="outline" size="xs" disabled={busy} />}>
                  {t("dashboard:unregistered_use_as_episode")}
                  <ChevronDown aria-hidden data-icon="inline-end" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="max-w-80">
                  <DropdownMenuItem onClick={() => assign(file.name, null)}>
                    {t("dashboard:unregistered_target_new_episode")}
                  </DropdownMenuItem>
                  {noSourceEpisodes.length > 0 ? <DropdownMenuSeparator /> : null}
                  {noSourceEpisodes.map((episode) => (
                    <DropdownMenuItem key={episode.episode} onClick={() => assign(file.name, episode.episode)}>
                      <TruncatedText
                        focusable={false}
                        text={t("dashboard:unregistered_target_episode", {
                          position: episodePosition(episodes, episode.episode),
                          title: episode.title?.trim() || t("dashboard:episodes_view_untitled"),
                        })}
                      />
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            {!file.can_join_whole_source ? (
              <p className="text-xs text-muted-foreground">{t("dashboard:unregistered_cannot_join")}</p>
            ) : null}
          </li>
        ))}
      </ul>
      <ImpactConfirmDialog
        request={
          deleting === null
            ? null
            : {
                title: t("dashboard:unregistered_delete_title", { name: deleting }),
                body: <p>{t("dashboard:unregistered_delete_desc")}</p>,
                confirmLabel: t("dashboard:unregistered_delete_confirm"),
                destructive: true,
              }
        }
        busy={busy}
        onConfirm={() => {
          if (deleting) void run(deleting, () => API.deleteSourceFile(projectName, deleting));
        }}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
