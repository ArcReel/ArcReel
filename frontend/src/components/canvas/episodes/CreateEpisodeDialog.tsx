import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import type { SourceKind } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

import { SourceKindSelect } from "./SourceKindSelect";

interface CreateEpisodeDialogProps {
  projectName: string;
  /** 默认插在哪一集之后（集 ID）；缺省放在播出顺序末尾。 */
  initialAfter?: number | null;
  onClose: () => void;
  /** 新建成功。收到新集 ID；随后的项目刷新没成功时为 null，新集不在项目数据里，不定位。 */
  onCreated: (episode: number | null) => void;
}

/**
 * 新建一集：选位置（插在任意一集之后，默认放在末尾），标题、钩子和原文都可选。
 * 标题留空时界面按播出位置显示「第 N 集」，插入或调序后随之变化。挂载即打开，关闭由调用方卸载。
 */
export function CreateEpisodeDialog({ projectName, initialAfter = null, onClose, onCreated }: CreateEpisodeDialogProps) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        // 新建请求在途时不响应 Esc 与遮罩点击，避免集已建好却没有回到列表
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent showCloseButton={!busy}>
        <CreateEpisodeForm
          projectName={projectName}
          initialAfter={initialAfter}
          busy={busy}
          setBusy={setBusy}
          onCreated={onCreated}
        />
      </DialogContent>
    </Dialog>
  );
}

function CreateEpisodeForm({
  projectName,
  initialAfter,
  busy,
  setBusy,
  onCreated,
}: {
  projectName: string;
  initialAfter: number | null;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onCreated: (episode: number | null) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const fieldId = useId();
  const project = useProjectsStore((s) => s.currentProjectData);
  const episodes = project?.episodes ?? [];
  const withSourceKind = project?.content_mode === "drama";

  // 位置：null 放在末尾，数字为插在这一集之后
  const [after, setAfter] = useState<number | null>(
    initialAfter !== null && episodes.some((ep) => ep.episode === initialAfter) ? initialAfter : null,
  );
  const [title, setTitle] = useState("");
  const [hook, setHook] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [sourceKind, setSourceKind] = useState<SourceKind>("novel");
  const [error, setError] = useState<string | null>(null);

  const position = after === null ? episodes.length + 1 : episodes.findIndex((ep) => ep.episode === after) + 2;
  const hasSource = sourceText.trim().length > 0;
  const positions = [
    { value: null, label: t("dashboard:episode_create_position_end") },
    ...episodes.map((ep) => ({
      value: ep.episode,
      label: t("dashboard:episode_create_position_after", { name: episodeDisplayName(episodes, ep.episode, t) }),
    })),
  ];

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { episode } = await API.createEpisode(projectName, {
        after,
        title: title.trim(),
        hook: hook.trim(),
        source_text: hasSource ? sourceText : null,
        source_kind: hasSource && withSourceKind ? sourceKind : null,
      });
      // 刷新没成功时新集不在项目数据里，不定位过去
      onCreated((await refreshAfterWrite(projectName, t)) === "success" ? episode : null);
    } catch (err) {
      setError(t("dashboard:episode_create_failed", { message: errMsg(err) }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("dashboard:episode_create_title")}</DialogTitle>
      </DialogHeader>
      <DialogBody>
        <form
          id={formId}
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label id={`${fieldId}-position-label`}>{t("dashboard:episode_create_position")}</Label>
            <Select items={positions} value={after} onValueChange={setAfter} disabled={busy}>
              <SelectTrigger aria-labelledby={`${fieldId}-position-label`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false} align="start">
                {positions.map((item) => (
                  <SelectItem key={item.value ?? "end"} value={item.value}>
                    <TruncatedText text={item.label} focusable={false} />
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${fieldId}-title`}>{t("dashboard:episode_create_title_label")}</Label>
            <Input
              id={`${fieldId}-title`}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={t("common:episode_position_name", { position })}
              maxLength={200}
              disabled={busy}
              aria-describedby={`${fieldId}-title-hint`}
            />
            <p id={`${fieldId}-title-hint`} className="text-xs text-muted-foreground">
              {t("dashboard:episode_create_title_hint", { position })}
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${fieldId}-hook`}>
              {t("dashboard:episode_create_hook_label")}{" "}
              <span className="font-normal text-muted-foreground">{t("dashboard:episode_create_optional")}</span>
            </Label>
            <Input
              id={`${fieldId}-hook`}
              value={hook}
              onChange={(event) => setHook(event.target.value)}
              maxLength={2000}
              disabled={busy}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${fieldId}-source`}>
              {t("dashboard:episode_create_source_label")}{" "}
              <span className="font-normal text-muted-foreground">{t("dashboard:episode_create_optional")}</span>
            </Label>
            <Textarea
              id={`${fieldId}-source`}
              value={sourceText}
              onChange={(event) => setSourceText(event.target.value)}
              disabled={busy}
              className="min-h-32"
              aria-describedby={`${fieldId}-source-hint`}
            />
            <p id={`${fieldId}-source-hint`} className="text-xs text-muted-foreground">
              {t("dashboard:episode_create_source_hint")}
            </p>
            {withSourceKind && hasSource ? (
              <div className="flex items-center gap-2 text-muted-foreground">
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
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </form>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={busy} />}>{t("common:cancel")}</DialogClose>
        <Button type="submit" form={formId} disabled={busy}>
          {busy ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
          {t("dashboard:episode_create_submit")}
        </Button>
      </DialogFooter>
    </>
  );
}
