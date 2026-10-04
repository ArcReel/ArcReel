import { useEffect, useId, useRef, useState } from "react";
import { CircleAlert, CircleCheck, ImagePlus, Loader2, Send, TriangleAlert, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Input } from "@/components/ui/input";
import type { MarketSubmission, MarketSubmissionDiagnostic, MarketSubmissionIcon } from "@/types";
import { errMsg } from "@/utils/async";

const CHECK_DEBOUNCE_MS = 300;
const SLUG_MAX_LENGTH = 64;
const ICON_FILENAMES: Record<string, MarketSubmissionIcon["filename"]> = {
  png: "icon.png",
  webp: "icon.webp",
  svg: "icon.svg",
};

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/** 本地或官方服务预检的诊断清单；422 响应的 `diagnostic.diagnostics`。 */
function diagnosticsOf(error: unknown): MarketSubmissionDiagnostic[] | null {
  if (!(error instanceof ApiRequestError) || error.status !== 422) return null;
  const raw =
    typeof error.diagnostic === "object" && error.diagnostic !== null
      ? (error.diagnostic as { diagnostics?: unknown }).diagnostics
      : undefined;
  if (!Array.isArray(raw)) return null;
  return raw.filter(
    (item): item is MarketSubmissionDiagnostic =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as MarketSubmissionDiagnostic).file === "string" &&
      typeof (item as MarketSubmissionDiagnostic).message === "string",
  );
}

function DiagnosticList({ diagnostics }: { diagnostics: MarketSubmissionDiagnostic[] }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <p aria-live="polite" className="border-b border-border px-4 py-2.5 text-sm font-medium">
        {t("market_share_diagnostics_summary", { count: diagnostics.length })}
      </p>
      <ul className="divide-y divide-border">
        {diagnostics.map((diagnostic, index) => (
          <li
            key={`${diagnostic.file}-${diagnostic.path}-${diagnostic.code}-${index}`}
            className="flex items-start gap-2.5 px-4 py-2.5"
          >
            <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
            <span className="min-w-0 flex-1 text-sm text-subtle-foreground">
              <span className="mr-2 font-mono text-xs text-muted-foreground" translate="no">
                {diagnostic.file ? diagnostic.file : "slug"}
              </span>
              {diagnostic.message}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 分享到官方市场：先展示本地校验结果，通过后才可提交。提交的是端点已保存的定义与可选图标；
 * 同一 slug 的提交在 PR 合并前再次提交会进入同一个 PR。
 */
export function ShareToMarketDialog({
  endpointId,
  initialSlug,
  onClose,
  onSubmitted,
}: {
  endpointId: number;
  initialSlug: string;
  onClose: () => void;
  onSubmitted: (submission: MarketSubmission) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const slugId = useId();
  const slugHintId = useId();
  const usernameId = useId();
  const usernameHintId = useId();
  const iconLabelId = useId();
  const iconInput = useRef<HTMLInputElement>(null);
  const [slug, setSlug] = useState(initialSlug);
  const [githubUsername, setGithubUsername] = useState("");
  const [icon, setIcon] = useState<{ name: string; value: MarketSubmissionIcon } | null>(null);
  // 每次选择或移除图标都递增；读取完成时序号已变说明被更新的选择取代，结果丢弃。
  const iconRead = useRef(0);
  const [readingIcon, setReadingIcon] = useState(false);
  const [iconError, setIconError] = useState<string | null>(null);
  // null：校验中或尚未取回。
  const [diagnostics, setDiagnostics] = useState<MarketSubmissionDiagnostic[] | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const iconValue = icon?.value ?? null;
  useEffect(() => {
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 预检请求换参时立即清除旧结果，避免等待期间提交未校验的内容
    setDiagnostics(null);
    setCheckError(null);
    const timer = setTimeout(() => {
      API.checkMarketSubmission({ endpoint_id: endpointId, slug, icon: iconValue }, { signal: controller.signal })
        .then((result) => {
          if (!controller.signal.aborted) setDiagnostics(result.diagnostics);
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) setCheckError(errMsg(error));
        });
    }, CHECK_DEBOUNCE_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [endpointId, slug, iconValue]);

  const pickIcon = async (file: File | undefined) => {
    if (!file) return;
    const read = ++iconRead.current;
    const filename = ICON_FILENAMES[file.name.split(".").pop()?.toLowerCase() ?? ""];
    if (!filename) {
      setReadingIcon(false);
      setIconError(t("market_share_icon_format"));
      return;
    }
    setIconError(null);
    setReadingIcon(true);
    try {
      const content = toBase64(await file.arrayBuffer());
      if (read === iconRead.current) setIcon({ name: file.name, value: { filename, content } });
    } catch (error) {
      if (read === iconRead.current) setIconError(errMsg(error));
    } finally {
      if (read === iconRead.current) setReadingIcon(false);
    }
  };

  const removeIcon = () => {
    iconRead.current += 1;
    setReadingIcon(false);
    setIcon(null);
  };

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const submission = await API.createMarketSubmission({
        endpoint_id: endpointId,
        slug,
        icon: iconValue,
        github_username: githubUsername.trim() || null,
      });
      onSubmitted(submission);
    } catch (error) {
      const remote = diagnosticsOf(error);
      if (remote) setDiagnostics(remote);
      setSubmitError(errMsg(error, t("market_share_failed")));
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit =
    !submitting && !readingIcon && slug !== "" && diagnostics !== null && diagnostics.length === 0;

  return (
    <Dialog
      open
      onOpenChange={(next: boolean) => {
        // 提交在途时不响应 Esc 与遮罩点击，避免对话框先于结果消失
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{t("market_share_title")}</DialogTitle>
          <DialogDescription>{t("market_share_intro")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-5">
            <Alert>
              <TriangleAlert aria-hidden className="text-warn" />
              <AlertDescription>{t("market_share_no_credentials")}</AlertDescription>
            </Alert>

            <div className="flex flex-col gap-1.5">
              <label htmlFor={slugId} className="text-sm font-medium">
                {t("market_share_slug_label")}
              </label>
              <Input
                id={slugId}
                value={slug}
                maxLength={SLUG_MAX_LENGTH}
                disabled={submitting}
                autoComplete="off"
                spellCheck={false}
                aria-describedby={slugHintId}
                onChange={(event) => setSlug(event.target.value.trim())}
              />
              <p id={slugHintId} className="text-xs text-muted-foreground">
                {t("market_share_slug_hint")}
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor={usernameId} className="text-sm font-medium">
                {t("market_share_github_label")}
              </label>
              <Input
                id={usernameId}
                value={githubUsername}
                maxLength={39}
                disabled={submitting}
                autoComplete="off"
                spellCheck={false}
                placeholder="octocat"
                aria-describedby={usernameHintId}
                onChange={(event) => setGithubUsername(event.target.value)}
              />
              <p id={usernameHintId} className="text-xs text-muted-foreground">
                {t("market_share_github_hint")}
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <span id={iconLabelId} className="text-sm font-medium">
                {t("market_share_icon_label")}
              </span>
              <div role="group" aria-labelledby={iconLabelId} className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={submitting}
                  onClick={() => iconInput.current?.click()}
                >
                  <ImagePlus aria-hidden data-icon="inline-start" />
                  {icon ? icon.name : t("market_share_icon_pick")}
                </Button>
                {icon && (
                  <Button variant="ghost" size="sm" disabled={submitting} onClick={removeIcon}>
                    <X aria-hidden data-icon="inline-start" />
                    {t("market_share_icon_remove")}
                  </Button>
                )}
                <input
                  ref={iconInput}
                  type="file"
                  accept=".png,.webp,.svg,image/png,image/webp,image/svg+xml"
                  className="hidden"
                  data-testid="market-share-icon-input"
                  onChange={(event) => {
                    void pickIcon(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                />
              </div>
              <p className="text-xs text-muted-foreground">{t("market_share_icon_hint")}</p>
              {iconError && (
                <p role="alert" className="text-sm text-warn">
                  {iconError}
                </p>
              )}
            </div>

            {checkError ? (
              <p role="alert" className="text-sm text-warn">
                {t("market_share_check_failed", { message: checkError })}
              </p>
            ) : diagnostics === null ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
                {t("market_share_checking")}
              </p>
            ) : diagnostics.length === 0 ? (
              <p aria-live="polite" className="flex items-center gap-2 text-sm text-subtle-foreground">
                <CircleCheck aria-hidden className="size-4 shrink-0 text-good" />
                {t("market_share_check_passed")}
              </p>
            ) : (
              <DiagnosticList diagnostics={diagnostics} />
            )}

            {submitError && (
              <p role="alert" className="text-sm text-destructive">
                {submitError}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            {t("common:cancel")}
          </Button>
          <Button disabled={!canSubmit} onClick={() => void submit()}>
            {submitting ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Send aria-hidden data-icon="inline-start" />
            )}
            {t("market_share_submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
