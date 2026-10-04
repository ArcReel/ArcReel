/**
 * 成片投递弹窗：选平台 → 填文案 → 投递 → 看每个平台的结果。
 *
 * 平台清单取自「该档案已连接的账号」与「接受视频的平台」的交集，而不是固定列表：让人勾选
 * 一个没连号的平台，只能换来一次上游 skipped，用户却以为发出去了。
 *
 * 投递后按 request_id 轮询到终态。终态之前不关窗，也不把「已受理」当成「已发布」——两者
 * 之间隔着各平台的转码与审核，混为一谈会让失败无人知晓。
 *
 * 由调用方按需挂载（关闭即卸载），本组件不负责自清：常驻渲染会让下次打开直接看到上一次的
 * 结果面板，且轮询定时器还在跑。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, ExternalLink, Loader2, MinusCircle, XCircle } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { InlineWarning } from "@/components/shared/InlineWarning";
import type { PresentationResourceType, PresentationVariant } from "@/types/presentation";
import type {
  ConnectedSocialAccount,
  SocialPlatformOutcome,
  SocialPublishProgress,
} from "@/types/social-publish";
import { errMsg } from "@/utils/async";

const POLL_INTERVAL_MS = 5000;

/** 投递标识，同时是上游幂等键；形状要与服务端的校验一致。 */
function newRequestId(): string {
  const uuid = globalThis.crypto?.randomUUID?.().replace(/-/g, "");
  // randomUUID 只在安全上下文里有；退路只要够唯一即可，这不是凭证。
  const token = uuid ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  return `arcreel-${token}`;
}

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
  // 每完成一次查询就自增，轮询 effect 据此排下一次：成功与失败都要排，否则一次网络抖动
  // 就让轮询永久停摆，界面停在非终态。
  const [pollTick, setPollTick] = useState(0);
  const requestIdRef = useRef<string | null>(null);
  // 同一次投递的重试要带同一个 id（见 handlePublish）。
  const attemptIdRef = useRef<string | null>(null);

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

  const fetchProgress = useCallback(async (requestId: string) => {
    try {
      setProgress(await API.getSocialPublishStatus(requestId));
    } catch {
      // 轮询失败不清空已知进度：一次网络抖动不该让结果面板变回空白。
    } finally {
      setPollTick((tick) => tick + 1);
    }
  }, []);

  useEffect(() => {
    if (progress === null || progress.terminal) return;
    const requestId = requestIdRef.current;
    if (requestId === null) return;
    // 间隔恒定：曾按「结果还空着就立刻再问」提速，可上游在排队阶段本来就回空结果，
    // 于是每次响应都触发下一次零延迟查询，转成一条打满浏览器与后端的死循环。
    // 首帧进度改由 handlePublish 提交成功后主动拉一次。
    const timer = window.setTimeout(() => void fetchProgress(requestId), POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [progress, pollTick, fetchProgress]);

  const togglePlatform = (platform: string) => {
    setSelected((prev) =>
      prev.includes(platform) ? prev.filter((entry) => entry !== platform) : [...prev, platform],
    );
  };

  const handlePublish = async () => {
    setSubmitting(true);
    setSubmitError(null);
    // 重试必须复用同一个 id：上传可能已被上游受理、响应却丢在半路（超时、504），本端这一侧
    // 什么都没留下。换个新 id 重试会被上游当成新投递，把同一条成片发两遍，而社交平台侧
    // 无法回滚。整个弹窗周期内它只生成一次，关闭时随其余状态清掉。
    const requestId = attemptIdRef.current ?? newRequestId();
    attemptIdRef.current = requestId;
    try {
      const submission = await API.publishPresentation(projectName, resourceType, resourceId, {
        platforms: selected,
        title: title.trim(),
        variant,
        video_version: videoVersion,
        audio_version: audioVersion,
        description: description.trim() || undefined,
        // datetime-local 给的是本机挂钟时间，转成带 Z 的绝对时刻再发。上游按偏移量优先：
        // 带 Z 时以它为准，timezone 只用于渲染回显；不带偏移量的字符串才按 timezone 解释。
        // 对 QA 环境实测过（含与本机不同的 timezone），两种写法落到同一个时刻，不会叠加偏移。
        scheduled_date: scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
        timezone: scheduledAt ? Intl.DateTimeFormat().resolvedOptions().timeZone : undefined,
        request_id: requestId,
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
      // 受理回执不带任何平台结果，主动拉一次首帧，之后交给恒定间隔的轮询。
      void fetchProgress(submission.request_id);
    } catch (err) {
      setSubmitError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  const canPublish = selected.length > 0 && title.trim().length > 0 && !submitting;
  // 提交在途时不许关：此刻上游可能已经受理、响应还没回来，而 request_id 只活在本组件里
  // （关闭即卸载）。这时关掉再重开会现生成新的 id，重试就成了第二次投递。受理之后再关是
  // 安全的——那时 id 已经用掉了，重开是一次全新的投递，不是重试。
  const canClose = !submitting;
  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen && canClose) onClose();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("dashboard:social_publish_dialog_title", { unit: resourceId })}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4">
            {loadError && <InlineWarning message={loadError} />}

            {progress === null ? (
              <>
                <div className="flex flex-col gap-2">
                  <span className="text-sm font-medium">{t("dashboard:social_publish_platforms_label")}</span>
                  {accounts === null ? (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
                  ) : accounts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("dashboard:social_publish_no_connected_accounts")}</p>
                  ) : (
                    <ul className="flex flex-wrap gap-x-4 gap-y-2">
                      {accounts.map((account) => (
                        <li key={account.platform}>
                          <Label>
                            <Checkbox
                              checked={selected.includes(account.platform)}
                              disabled={account.reauth_required}
                              onCheckedChange={() => togglePlatform(account.platform)}
                            />
                            {account.platform}
                            {account.reauth_required && (
                              <span className="text-xs text-warn">{t("dashboard:social_publish_reauth_required")}</span>
                            )}
                          </Label>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="publish-title">{t("dashboard:social_publish_title_label")}</Label>
                  <Input id="publish-title" type="text" value={title} onChange={(event) => setTitle(event.target.value)} />
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="publish-description">{t("dashboard:social_publish_description_label")}</Label>
                  <Textarea
                    id="publish-description"
                    aria-describedby="publish-description-hint"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                  />
                  <p id="publish-description-hint" className="text-xs text-muted-foreground">
                    {t("dashboard:social_publish_description_hint")}
                  </p>
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="publish-scheduled-at">{t("dashboard:social_publish_schedule_label")}</Label>
                  <Input
                    id="publish-scheduled-at"
                    type="datetime-local"
                    aria-describedby="publish-scheduled-at-hint"
                    value={scheduledAt}
                    onChange={(event) => setScheduledAt(event.target.value)}
                  />
                  <p id="publish-scheduled-at-hint" className="text-xs text-muted-foreground">
                    {t("dashboard:social_publish_schedule_hint")}
                  </p>
                </div>

                {submitError && <InlineWarning message={submitError} />}
              </>
            ) : (
              <PublishProgressPanel progress={progress} />
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          {progress === null ? (
            <>
              <Button variant="outline" onClick={onClose} disabled={!canClose}>
                {t("common:cancel")}
              </Button>
              <Button onClick={() => void handlePublish()} disabled={!canPublish}>
                {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                {t("dashboard:social_publish_submit")}
              </Button>
            </>
          ) : (
            <Button variant="outline" onClick={onClose}>
              {t("common:close")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PublishProgressPanel({ progress }: { progress: SocialPublishProgress }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">
        {progress.terminal
          ? t("social_publish_finished", { completed: progress.completed, total: progress.total })
          : t("social_publish_in_flight", { completed: progress.completed, total: progress.total })}
      </p>

      <ul className="flex flex-col gap-1.5">
        {progress.outcomes.map((outcome) => (
          <PublishOutcomeRow key={outcome.platform} outcome={outcome} />
        ))}
      </ul>
    </div>
  );
}

function PublishOutcomeRow({ outcome }: { outcome: SocialPlatformOutcome }) {
  const { t } = useTranslation("dashboard");
  return (
    <li className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-sm">
      {outcome.status === "completed" ? (
        <CheckCircle2 className="size-3.5 shrink-0 text-good" aria-hidden />
      ) : outcome.status === "failed" ? (
        <XCircle className="size-3.5 shrink-0 text-destructive" aria-hidden />
      ) : outcome.status === "skipped" ? (
        // 也是终态：档案没连这个平台。画成转圈会让它在轮询停止后永远转下去。
        <MinusCircle className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      ) : (
        <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />
      )}
      <span>{outcome.platform}</span>
      <span className="text-muted-foreground">{t(`social_publish_status_${outcome.status}`)}</span>
      <span className="flex-1" />
      {outcome.url && (
        <a
          href={outcome.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
        >
          {t("social_publish_open_post")}
          <ExternalLink className="size-3" aria-hidden />
        </a>
      )}
      {outcome.error && <span className="text-destructive">{outcome.error}</span>}
    </li>
  );
}
