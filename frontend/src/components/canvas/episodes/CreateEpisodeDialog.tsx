import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { SourceKind } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

import { SourceKindSelect } from "./SourceKindSelect";

const END = "end";

interface CreateEpisodeDialogProps {
  projectName: string;
  /** 默认插在哪一集之后（集 ID）；缺省放在播出顺序末尾。 */
  initialAfter?: number | null;
  onClose: () => void;
  /** 新建成功，项目数据已刷新。 */
  onCreated: (episode: number) => void;
}

/**
 * 新建一集：选位置（插在任意一集之后，默认放在末尾），标题、钩子和原文都可选。
 * 标题留空时界面按播出位置显示「第 N 集」，插入或调序后随之变化。
 */
export function CreateEpisodeDialog({ projectName, initialAfter = null, onClose, onCreated }: CreateEpisodeDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const titleId = useId();
  const fieldId = useId();
  const project = useProjectsStore((s) => s.currentProjectData);
  const episodes = project?.episodes ?? [];
  const withSourceKind = project?.content_mode === "drama";

  const [after, setAfter] = useState<string>(
    initialAfter !== null && episodes.some((ep) => ep.episode === initialAfter) ? String(initialAfter) : END,
  );
  const [title, setTitle] = useState("");
  const [hook, setHook] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [sourceKind, setSourceKind] = useState<SourceKind>("novel");
  const [busy, setBusy] = useState(false);

  const afterId = after === END ? null : Number(after);
  const position =
    afterId === null ? episodes.length + 1 : episodes.findIndex((ep) => ep.episode === afterId) + 2;
  const hasSource = sourceText.trim().length > 0;

  const submit = async () => {
    setBusy(true);
    try {
      const { episode } = await API.createEpisode(projectName, {
        after: afterId,
        title: title.trim(),
        hook: hook.trim(),
        source_text: hasSource ? sourceText : null,
        source_kind: hasSource && withSourceKind ? sourceKind : null,
      });
      await useProjectsStore.getState().refreshProject(projectName);
      useAppStore
        .getState()
        .pushToast(
          t("dashboard:episode_create_done", { name: title.trim() || t("common:episode_position_name", { position }) }),
          "success",
        );
      onCreated(episode);
    } catch (err) {
      useAppStore.getState().pushToast(t("dashboard:episode_create_failed", { message: errMsg(err) }), "error");
      setBusy(false);
    }
  };

  return (
    <GlassModal open onClose={busy ? () => {} : onClose} labelledBy={titleId} closeOnBackdrop={!busy} closeOnEscape={!busy}>
      <form
        className="flex max-h-[min(86vh,720px)] flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) void submit();
        }}
      >
        <header className="flex items-start justify-between gap-3 px-6 pt-5">
          <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-text">
            {t("dashboard:episode_create_title")}
          </h2>
          <ModalCloseButton onClick={onClose} disabled={busy} />
        </header>

        <div className="mt-4 min-h-0 flex-1 space-y-4 overflow-y-auto px-6">
          <div>
            <FieldLabel htmlFor={`${fieldId}-position`}>{t("dashboard:episode_create_position")}</FieldLabel>
            <select
              id={`${fieldId}-position`}
              value={after}
              onChange={(event) => setAfter(event.target.value)}
              className={INPUT_CLS}
              disabled={busy}
            >
              <option value={END}>{t("dashboard:episode_create_position_end")}</option>
              {episodes.map((ep) => (
                <option key={ep.episode} value={String(ep.episode)}>
                  {t("dashboard:episode_create_position_after", { name: episodeDisplayName(episodes, ep.episode, t) })}
                </option>
              ))}
            </select>
          </div>

          <div>
            <FieldLabel htmlFor={`${fieldId}-title`}>{t("dashboard:episode_create_title_label")}</FieldLabel>
            <input
              id={`${fieldId}-title`}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={t("common:episode_position_name", { position })}
              maxLength={200}
              className={INPUT_CLS}
              disabled={busy}
            />
            <p className="mt-1 text-[11.5px] leading-[1.6] text-text-4">
              {t("dashboard:episode_create_title_hint", { position })}
            </p>
          </div>

          <div>
            <FieldLabel htmlFor={`${fieldId}-hook`} trailing={<OptionalTag />}>
              {t("dashboard:episode_create_hook_label")}
            </FieldLabel>
            <input
              id={`${fieldId}-hook`}
              value={hook}
              onChange={(event) => setHook(event.target.value)}
              maxLength={2000}
              className={INPUT_CLS}
              disabled={busy}
            />
          </div>

          <div>
            <FieldLabel htmlFor={`${fieldId}-source`} trailing={<OptionalTag />}>
              {t("dashboard:episode_create_source_label")}
            </FieldLabel>
            <textarea
              id={`${fieldId}-source`}
              value={sourceText}
              onChange={(event) => setSourceText(event.target.value)}
              rows={6}
              className={`${INPUT_CLS} resize-y leading-[1.7]`}
              disabled={busy}
            />
            <p className="mt-1 text-[11.5px] leading-[1.6] text-text-4">{t("dashboard:episode_create_source_hint")}</p>
            {withSourceKind && hasSource ? (
              <div className="mt-2 flex items-center gap-2 text-[12px] text-text-3">
                <span>{t("dashboard:source_kind")}</span>
                <SourceKindSelect
                  value={sourceKind}
                  onChange={setSourceKind}
                  disabled={busy}
                  label={t("dashboard:source_kind")}
                />
              </div>
            ) : null}
          </div>
        </div>

        <footer className="flex justify-end gap-2 px-6 pb-5 pt-4">
          <SecondaryButton type="button" onClick={onClose} disabled={busy}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={busy}>
            {t("dashboard:episode_create_submit")}
          </PrimaryButton>
        </footer>
      </form>
    </GlassModal>
  );
}

function OptionalTag() {
  const { t } = useTranslation("dashboard");
  return <span className="text-[11px] font-normal normal-case tracking-normal text-text-4">{t("episode_create_optional")}</span>;
}
