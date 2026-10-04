import { useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta } from "@/types";
import type { EpisodesViewFile, SourceKind } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

import { ImpactConfirmDialog } from "./ImpactConfirmDialog";
import { SourceKindSelect } from "./SourceKindSelect";

interface SourceFileKindControlProps {
  projectName: string;
  file: EpisodesViewFile;
  episodes: EpisodeMeta[];
}

/**
 * 文件条上的源文件类型下拉。改类型不改原文，也不动分集。
 *
 * 会让已开始制作的集的脚本规划判 stale 时，先列出这些集请创作者确认；没有这类集时直接改。
 * 文件在 ArcReel 之外被改动过、还没有更新分集账本时暂停。
 */
export function SourceFileKindControl({ projectName, file, episodes }: SourceFileKindControlProps) {
  const { t } = useTranslation("dashboard");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ kind: SourceKind; episodes: number[] } | null>(null);

  if (file.source_kind === null) return null;

  const apply = async (kind: SourceKind, confirm: boolean) => {
    setBusy(true);
    try {
      const result = await API.setSourceFileKind(projectName, file.name, kind, confirm);
      if (result.needs_confirmation) {
        setPending({ kind, episodes: result.affected_episodes });
        return;
      }
      setPending(null);
      if (result.applied) await useProjectsStore.getState().refreshProject(projectName);
    } catch (err) {
      useAppStore.getState().pushToast(t("source_kind_change_failed", { message: errMsg(err) }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <SourceKindSelect
        value={file.source_kind}
        onChange={(kind) => void apply(kind, false)}
        disabled={busy || file.missing || file.changed_outside}
        label={t("source_kind_of", { name: file.name })}
      />
      <ImpactConfirmDialog
        request={
          pending === null
            ? null
            : {
                title: t("source_kind_change_title", { name: file.name }),
                body: (
                  <div className="flex flex-col gap-2">
                    <p>{t("source_kind_change_desc")}</p>
                    <ul className="list-disc pl-5">
                      {pending.episodes.map((episode) => (
                        <li key={episode}>
                          {t("source_kind_change_episode", {
                            position: episodePosition(episodes, episode) ?? "?",
                            name: episodeDisplayName(episodes, episode, t),
                          })}
                        </li>
                      ))}
                    </ul>
                  </div>
                ),
                confirmLabel: t("source_kind_change_confirm"),
                destructive: false,
              }
        }
        busy={busy}
        onConfirm={() => {
          if (pending) void apply(pending.kind, true);
        }}
        onCancel={() => setPending(null)}
      />
    </>
  );
}
