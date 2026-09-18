/**
 * 成片投递弹窗：选平台 → 填文案 → 投递 → 看每个平台的结果。
 *
 * 平台清单取自「该档案已连接的账号」与「接受视频的平台」的交集，而不是固定列表：让人勾选
 * 一个没连号的平台，只能换来一次上游 skipped，用户却以为发出去了。
 *
 * 投递后按 request_id 轮询到终态。终态之前不关窗，也不把「已受理」当成「已发布」——两者
 * 之间隔着各平台的转码与审核，混为一谈会让失败无人知晓。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react";
import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { InlineWarning } from "@/components/ui/InlineWarning";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE } from "@/components/ui/darkroom-tokens";
import type { PresentationResourceType, PresentationVariant } from "@/types/presentation";
import type {
  ConnectedSocialAccount,
  SocialPlatformOutcome,
  SocialPublishProgress,
} from "@/types/social-publish";
import { errMsg } from "@/utils/async";

const POLL_INTERVAL_MS = 5000;

const INPUT_CLS =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
const LABEL_CLS =
  "mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4";

export interface PublishToSocialDialogProps {
  open: boolean;
  onClose: () => void;
  projectName: string;
  resourceType: PresentationResourceType;
  resourceId: string;
  variant: PresentationVariant;
  videoVersion?: number;
  audioVersion?: number;
}

export function PublishToSocialDialog({
  open,
  onClose,
  projectName,
  resourceType,
  resourceId,
  variant,
  videoVersion,
  audioVersion,
}: PublishToSocialDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);

  const [accounts, setAccounts] = useState<ConnectedSocialAccount[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [title, setTitle] = useState(resourceId);
  const [description, setDescription] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [progress, setProgress] = useState<SocialPublishProgress | null>(null);
  const requestIdRef = useRef<string | null>(null);

  const loadAccounts = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await API.getSocialPublishProfiles();
      const profile = res.profiles.find((entry) => entry.username === res.active_profile);
      const videoCapable = new Set(res.video_platforms);
      setAccounts((profile?.accounts ?? []).filter((account) => videoCapable.has(account.platform)));
    } catch (err) {
      setAccounts([]);
      setLoadError(errMsg(err));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    // 每次打开都重取：凭证或已连接账号可能在上一次打开之后变过。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadAccounts();
  }, [open, loadAccounts]);

  useEffect(() => {
    if (progress === null || progress.terminal) return;
    const requestId = requestIdRef.current;
    if (requestId === null) return;
    // 受理回执本身不带任何平台结果，等满一个轮询周期才去问会让面板空着 5 秒；
    // 之后的轮询才按周期走。
    const delay = progress.outcomes.length === 0 ? 0 : POLL_INTERVAL_MS;
    const timer = window.setTimeout(() => {
      void API.getSocialPublishStatus(requestId)
        .then(setProgress)
        // 轮询失败不清空已知进度：一次网络抖动不该让结果面板变回空白。
        .catch(() => undefined);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [progress]);

  const togglePlatform = (platform: string) => {
    setSelected((prev) =>
      prev.includes(platform) ? prev.filter((entry) => entry !== platform) : [...prev, platform],
    );
  };

  const handlePublish = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const submission = await API.publishPresentation(projectName, resourceType, resourceId, {
        platforms: selected,
        title: title.trim(),
        variant,
        video_version: videoVersion,
        audio_version: audioVersion,
        description: description.trim() || undefined,
        scheduled_date: scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
        timezone: scheduledAt ? Intl.DateTimeFormat().resolvedOptions().timeZone : undefined,
      });
      requestIdRef.current = submission.request_id;
      setProgress({
        request_id: submission.request_id,
        job_id: submission.job_id,
        status: "pending",
        completed: 0,
        total: submission.total_platforms,
        terminal: false,
        outcomes: [],
      });
    } catch (err) {
      setSubmitError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  const canPublish = selected.length > 0 && title.trim().length > 0 && !submitting;

  return (
    <GlassModal
      open={open}
      onClose={onClose}
      labelledBy="publish-to-social-title"
      widthClassName="w-full max-w-lg"
      panelClassName="max-h-[85vh] overflow-y-auto"
    >
      <div className="p-5">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
              Distribution
            </div>
            <h3 id="publish-to-social-title" className="mt-1 text-[15px] font-medium text-text">
              {t("dashboard:social_publish_dialog_title", { unit: resourceId })}
            </h3>
          </div>
          <ModalCloseButton onClick={onClose} />
        </div>

        {loadError && <InlineWarning message={loadError} className="mb-3" />}

        {progress === null ? (
          <div className="space-y-4">
            <div>
              <span className={LABEL_CLS}>{t("dashboard:social_publish_platforms_label")}</span>
              {accounts === null ? (
                <Loader2 className="h-4 w-4 motion-safe:animate-spin text-text-4" aria-hidden />
              ) : accounts.length === 0 ? (
                <p className="text-[12px] text-text-3">
                  {t("dashboard:social_publish_no_connected_accounts")}
                </p>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {accounts.map((account) => (
                    <li key={account.platform}>
                      <label
                        className={`flex cursor-pointer items-center gap-1.5 rounded-[6px] border px-2 py-1 text-[12px] ${
                          selected.includes(account.platform)
                            ? "border-accent text-text"
                            : "border-hairline text-text-2"
                        } ${account.reauth_required ? "cursor-not-allowed opacity-50" : ""}`}
                      >
                        <input
                          type="checkbox"
                          className="accent-accent"
                          checked={selected.includes(account.platform)}
                          disabled={account.reauth_required}
                          onChange={() => togglePlatform(account.platform)}
                        />
                        {account.platform}
                        {account.reauth_required && (
                          <span className="text-warm">
                            {t("dashboard:social_publish_reauth_required")}
                          </span>
                        )}
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <label htmlFor="publish-title" className={LABEL_CLS}>
                {t("dashboard:social_publish_title_label")}
              </label>
              <input
                id="publish-title"
                type="text"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                className={INPUT_CLS}
              />
            </div>

            <div>
              <label htmlFor="publish-description" className={LABEL_CLS}>
                {t("dashboard:social_publish_description_label")}
              </label>
              <textarea
                id="publish-description"
                rows={3}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className={INPUT_CLS}
              />
              <p className="mt-1 text-[11px] text-text-4">
                {t("dashboard:social_publish_description_hint")}
              </p>
            </div>

            <div>
              <label htmlFor="publish-scheduled-at" className={LABEL_CLS}>
                {t("dashboard:social_publish_schedule_label")}
              </label>
              <input
                id="publish-scheduled-at"
                type="datetime-local"
                value={scheduledAt}
                onChange={(event) => setScheduledAt(event.target.value)}
                className={INPUT_CLS}
              />
              <p className="mt-1 text-[11px] text-text-4">
                {t("dashboard:social_publish_schedule_hint")}
              </p>
            </div>

            {submitError && <InlineWarning message={submitError} />}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                {t("common:cancel")}
              </button>
              <button
                type="button"
                onClick={() => void handlePublish()}
                disabled={!canPublish}
                className={ACCENT_BTN_CLS}
                style={ACCENT_BUTTON_STYLE}
              >
                {submitting ? (
                  <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
                ) : null}
                {t("dashboard:social_publish_submit")}
              </button>
            </div>
          </div>
        ) : (
          <PublishProgressPanel progress={progress} onClose={onClose} />
        )}
      </div>
    </GlassModal>
  );
}

function PublishProgressPanel({
  progress,
  onClose,
}: {
  progress: SocialPublishProgress;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-text-2">
        {progress.terminal
          ? t("dashboard:social_publish_finished", {
              completed: progress.completed,
              total: progress.total,
            })
          : t("dashboard:social_publish_in_flight", {
              completed: progress.completed,
              total: progress.total,
            })}
      </p>

      <ul className="space-y-1.5">
        {progress.outcomes.map((outcome) => (
          <PublishOutcomeRow key={outcome.platform} outcome={outcome} />
        ))}
      </ul>

      <div className="flex justify-end pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {t("common:close")}
        </button>
      </div>
    </div>
  );
}

function PublishOutcomeRow({ outcome }: { outcome: SocialPlatformOutcome }) {
  const { t } = useTranslation("dashboard");
  return (
    <li className="flex items-center gap-2 rounded-[6px] border border-hairline px-2.5 py-1.5 text-[12px]">
      {outcome.status === "completed" ? (
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden />
      ) : outcome.status === "failed" ? (
        <XCircle className="h-3.5 w-3.5 shrink-0 text-warm" aria-hidden />
      ) : (
        <Loader2 className="h-3.5 w-3.5 shrink-0 motion-safe:animate-spin text-text-4" aria-hidden />
      )}
      <span className="text-text">{outcome.platform}</span>
      <span className="text-text-4">{t(`social_publish_status_${outcome.status}`)}</span>
      <span className="flex-1" />
      {outcome.url && (
        <a
          href={outcome.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-accent-2 hover:underline"
        >
          {t("social_publish_open_post")}
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      )}
      {outcome.error && <span className="text-warm">{outcome.error}</span>}
    </li>
  );
}
