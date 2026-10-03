import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, FileQuestion, Trash2 } from "lucide-react";

import { API } from "@/api";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { GHOST_BTN_CLS, ICON_BTN_CLS, INPUT_CLS } from "@/components/shared/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, UnregisteredSourceFile } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

const NEW_EPISODE = "new";

interface UnregisteredFilesPanelProps {
  projectName: string;
  files: UnregisteredSourceFile[];
  episodes: EpisodeMeta[];
  /** 处置完成后重新拉取「分集」视图。 */
  onChanged: () => void;
}

/**
 * 「有 N 个文件还没用上」：放在 source/ 里、却不属于整本源文也不是任何一集原文的文件，逐个选择
 * 加入整本源文、作为某一集的原文（新的一集或一集无原文的集），或者删除。
 */
export function UnregisteredFilesPanel({ projectName, files, episodes, onChanged }: UnregisteredFilesPanelProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [assigning, setAssigning] = useState<{ name: string; target: string } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const noSourceEpisodes = episodes.filter((episode) => episode.source_origin === "none");

  const run = async (name: string, action: () => Promise<unknown>, done: string) => {
    setBusy(name);
    try {
      await action();
      useAppStore.getState().pushToast(done, "success");
      setAssigning(null);
      setDeleting(null);
      await useProjectsStore.getState().refreshProject(projectName);
      onChanged();
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("dashboard:unregistered_action_failed", { name, message: errMsg(err) }), "error");
    } finally {
      setBusy(null);
    }
  };

  const joinWholeSource = (name: string) =>
    void run(
      name,
      () => API.adoptSourceFile(projectName, name, { target: "whole_source" }),
      t("dashboard:unregistered_joined_whole", { name }),
    );

  const assignToEpisode = (name: string, target: string) => {
    const episode = target === NEW_EPISODE ? null : Number(target);
    void run(
      name,
      () => API.adoptSourceFile(projectName, name, { target: "episode", episode }),
      episode === null
        ? t("dashboard:unregistered_used_new_episode", { name })
        : t("dashboard:unregistered_used_episode", { name, episode: episodeDisplayName(episodes, episode, t) }),
    );
  };

  const remove = (name: string) =>
    void run(name, () => API.deleteSourceFile(projectName, name), t("dashboard:unregistered_deleted", { name }));

  return (
    <section
      className="rounded-lg border px-3.5 py-3"
      style={{ borderColor: "color-mix(in oklab, var(--warn) 30%, transparent)", background: "color-mix(in oklab, var(--warn) 5%, transparent)" }}
    >
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls={panelId}
        className="focus-ring flex w-full items-center gap-2 rounded-md text-left"
      >
        <FileQuestion className="h-4 w-4 shrink-0 text-warn" aria-hidden />
        <span className="flex-1 text-[12.5px] font-medium text-foreground">
          {t("dashboard:unregistered_title", { count: files.length })}
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      <div id={panelId} hidden={!expanded}>
        <p className="mt-2 text-[11.5px] leading-[1.6] text-muted-foreground">{t("dashboard:unregistered_hint")}</p>
        <ul className="mt-2.5 space-y-2">
          {files.map((file) => {
            const isBusy = busy === file.name;
            const picking = assigning?.name === file.name ? assigning : null;
            return (
              <li
                key={file.name}
                className="rounded-md border border-border/50 px-2.5 py-2"
                style={{ background: "oklch(0.19 0.010 265 / 0.6)" }}
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground" title={file.name}>
                    {file.name}
                  </span>
                  <button
                    type="button"
                    className={ICON_BTN_CLS}
                    disabled={busy !== null}
                    onClick={() => setDeleting(file.name)}
                    aria-label={t("dashboard:unregistered_delete_aria", { name: file.name })}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
                {picking ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <select
                      value={picking.target}
                      onChange={(event) => setAssigning({ name: file.name, target: event.target.value })}
                      aria-label={t("dashboard:unregistered_episode_target", { name: file.name })}
                      className={`${INPUT_CLS} !w-auto min-w-0 flex-1 !py-1.5 !text-[12px]`}
                      disabled={isBusy}
                    >
                      <option value={NEW_EPISODE}>{t("dashboard:unregistered_target_new_episode")}</option>
                      {noSourceEpisodes.map((episode) => (
                        <option key={episode.episode} value={String(episode.episode)}>
                          {t("dashboard:unregistered_target_episode", {
                            position: episodePosition(episodes, episode.episode),
                            title: episode.title?.trim() || t("dashboard:episodes_view_untitled"),
                          })}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className={GHOST_BTN_CLS}
                      disabled={isBusy}
                      onClick={() => assignToEpisode(file.name, picking.target)}
                    >
                      {t("common:confirm")}
                    </button>
                    <button
                      type="button"
                      className="focus-ring rounded-md px-2 py-1 text-[12px] text-muted-foreground hover:text-foreground"
                      disabled={isBusy}
                      onClick={() => setAssigning(null)}
                    >
                      {t("common:cancel")}
                    </button>
                  </div>
                ) : (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className={GHOST_BTN_CLS}
                      disabled={busy !== null || !file.can_join_whole_source}
                      title={file.can_join_whole_source ? undefined : t("dashboard:unregistered_cannot_join")}
                      onClick={() => joinWholeSource(file.name)}
                    >
                      {t("dashboard:unregistered_join_whole")}
                    </button>
                    <button
                      type="button"
                      className={GHOST_BTN_CLS}
                      disabled={busy !== null}
                      onClick={() => setAssigning({ name: file.name, target: NEW_EPISODE })}
                    >
                      {t("dashboard:unregistered_use_as_episode")}
                    </button>
                  </div>
                )}
                {!file.can_join_whole_source && !picking ? (
                  <p className="mt-1.5 text-[11px] leading-[1.5] text-muted-foreground">{t("dashboard:unregistered_cannot_join")}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
      <ConfirmDialog
        open={deleting !== null}
        tone="danger"
        title={t("dashboard:unregistered_delete_title", { name: deleting ?? "" })}
        description={t("dashboard:unregistered_delete_desc")}
        confirmLabel={t("dashboard:unregistered_delete_confirm")}
        loading={busy !== null}
        onConfirm={() => {
          if (deleting) remove(deleting);
        }}
        onCancel={() => setDeleting(null)}
      />
    </section>
  );
}
