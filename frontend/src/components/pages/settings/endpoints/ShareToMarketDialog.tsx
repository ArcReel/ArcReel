import { useEffect, useId, useRef, useState } from "react";
import { CircleAlert, ImagePlus, Loader2, Send, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import type { MarketSubmission, MarketSubmissionDiagnostic, MarketSubmissionIcon } from "@/types";
import { errMsg } from "@/utils/async";
import { KICKER_ACCENT_CLS } from "../market/market-source-status";

const CHECK_DEBOUNCE_MS = 300;
const SLUG_MAX_LENGTH = 64;
const ICON_FILENAMES: Record<string, MarketSubmissionIcon["filename"]> = {
  png: "icon.png",
  webp: "icon.webp",
  svg: "icon.svg",
};

/** 由端点名称派生建议 slug：只保留小写字母、数字与连字符；名称里没有可用字符时为空，由用户填写。 */
export function slugFromName(name: string): string {
  return name
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .slice(0, SLUG_MAX_LENGTH)
    .replace(/-+$/, "");
}

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
    <div className="overflow-hidden rounded-[10px] border border-hairline" style={CARD_STYLE}>
      <div aria-live="polite" className="border-b border-hairline-soft px-4 py-2.5 text-[12.5px] font-medium text-text">
        {t("market_share_diagnostics_summary", { count: diagnostics.length })}
      </div>
      <ul>
        {diagnostics.map((diagnostic, index) => (
          <li
            key={`${diagnostic.file}-${diagnostic.path}-${diagnostic.code}-${index}`}
            className="flex items-start gap-2.5 border-b border-hairline-soft px-4 py-2.5 last:border-b-0"
          >
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warm-bright" aria-hidden />
            <span className="min-w-0 flex-1 text-[12.5px] leading-[1.55] text-text-2">
              <span className="mr-2 font-mono text-[11px] text-text-3">
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
  const titleId = useId();
  const slugId = useId();
  const slugHintId = useId();
  const usernameId = useId();
  const iconInput = useRef<HTMLInputElement>(null);
  const [slug, setSlug] = useState(initialSlug);
  const [githubUsername, setGithubUsername] = useState("");
  const [icon, setIcon] = useState<{ name: string; value: MarketSubmissionIcon } | null>(null);
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
    const filename = ICON_FILENAMES[file.name.split(".").pop()?.toLowerCase() ?? ""];
    if (!filename) {
      setIconError(t("market_share_icon_format"));
      return;
    }
    setIconError(null);
    setIcon({ name: file.name, value: { filename, content: toBase64(await file.arrayBuffer()) } });
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

  const canSubmit = !submitting && slug !== "" && diagnostics !== null && diagnostics.length === 0;

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-full max-w-xl"
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
    >
      <div className="flex max-h-[86vh] flex-col">
        <div className="flex items-center justify-between px-6 pt-5">
          <span className={KICKER_ACCENT_CLS}>Share to market</span>
          <ModalCloseButton onClick={onClose} disabled={submitting} />
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-4">
          <div>
            <h2 id={titleId} className="font-editorial text-[22px] text-text">
              {t("market_share_title")}
            </h2>
            <p className="mt-1.5 text-[12.5px] leading-[1.6] text-text-2">{t("market_share_intro")}</p>
          </div>

          <div className="rounded-[8px] border border-warn/30 bg-warn/8 px-3 py-2 text-[12px] leading-[1.55] text-text-2">
            {t("market_share_no_credentials")}
          </div>

          <div>
            <label htmlFor={slugId} className="mb-1 block text-[12px] text-text-2">
              {t("market_share_slug_label")}
            </label>
            <input
              id={slugId}
              className={`${INPUT_CLS} font-mono`}
              value={slug}
              maxLength={SLUG_MAX_LENGTH}
              disabled={submitting}
              aria-describedby={slugHintId}
              onChange={(event) => setSlug(event.target.value.trim())}
            />
            <p id={slugHintId} className="mt-1 text-[11.5px] leading-[1.5] text-text-3">
              {t("market_share_slug_hint")}
            </p>
          </div>

          <div>
            <label htmlFor={usernameId} className="mb-1 block text-[12px] text-text-2">
              {t("market_share_github_label")}
            </label>
            <input
              id={usernameId}
              className={INPUT_CLS}
              value={githubUsername}
              maxLength={39}
              disabled={submitting}
              placeholder="octocat"
              onChange={(event) => setGithubUsername(event.target.value)}
            />
            <p className="mt-1 text-[11.5px] leading-[1.5] text-text-3">{t("market_share_github_hint")}</p>
          </div>

          <div>
            <span className="mb-1 block text-[12px] text-text-2">{t("market_share_icon_label")}</span>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={GHOST_BTN_CLS}
                disabled={submitting}
                onClick={() => iconInput.current?.click()}
              >
                <ImagePlus className="h-3.5 w-3.5" aria-hidden />
                {icon ? icon.name : t("market_share_icon_pick")}
              </button>
              {icon && (
                <button
                  type="button"
                  className={GHOST_BTN_CLS}
                  disabled={submitting}
                  onClick={() => setIcon(null)}
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                  {t("market_share_icon_remove")}
                </button>
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
            <p className="mt-1 text-[11.5px] leading-[1.5] text-text-3">{t("market_share_icon_hint")}</p>
            {iconError && (
              <p role="alert" className="mt-1 text-[12px] text-warm-bright">
                {iconError}
              </p>
            )}
          </div>

          {checkError ? (
            <p role="alert" className="text-[12.5px] text-warm-bright">
              {t("market_share_check_failed", { message: checkError })}
            </p>
          ) : diagnostics === null ? (
            <div className="flex items-center gap-2 text-[12.5px] text-text-3">
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
              {t("market_share_checking")}
            </div>
          ) : diagnostics.length === 0 ? (
            <div
              aria-live="polite"
              className="rounded-[10px] border border-hairline px-4 py-2.5 text-[12.5px] text-text-2"
              style={CARD_STYLE}
            >
              {t("market_share_check_passed")}
            </div>
          ) : (
            <DiagnosticList diagnostics={diagnostics} />
          )}

          {submitError && (
            <p role="alert" className="text-[12.5px] text-warm-bright">
              {submitError}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-hairline-soft px-6 py-4">
          <button type="button" className={GHOST_BTN_CLS} onClick={onClose} disabled={submitting}>
            {t("common:cancel")}
          </button>
          <button
            type="button"
            className={ACCENT_BTN_SM_CLS}
            style={ACCENT_BUTTON_STYLE}
            disabled={!canSubmit}
            onClick={() => void submit()}
          >
            {submitting ? (
              <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />
            ) : (
              <Send className="h-3 w-3" aria-hidden />
            )}
            {t("market_share_submit")}
          </button>
        </div>
      </div>
    </GlassModal>
  );
}
