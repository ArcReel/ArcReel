import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, Eye, RefreshCw } from "lucide-react";
import { cn } from "cn";
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
import { errMsg } from "@/utils/async";
import { copyText } from "@/utils/clipboard";
import type { RenderedPromptPreview } from "@/types";

export interface PromptPreviewButtonProps<T extends RenderedPromptPreview> {
  /** Dialog titles, also trigger hover explanations distinguishing multiple entries on a page. */
  title: string;
  /** Fetch final prompts once per opening/refresh; calls read latest caller closures, e.g. current drafts. */
  load: (signal: AbortSignal) => Promise<T>;
  /** Scope explanations below titles, e.g. Rendered from saved contents or Computed from current model capabilities. */
  notice?: ReactNode;
  /** Additional blocks below final text receive complete current-request results, e.g. reference images sent with requests. */
  renderExtra?: (result: T) => ReactNode;
  disabled?: boolean;
  /** Save unsaved changes first; failures remain in place without previews. Callbacks require useCallback. */
  beforeOpen?: () => Promise<boolean>;
  saveFirst?: boolean;
}

/**
 * View prompt buttons/final-prompt dialogs: request on opening only, with copy/rerender actions inside.
 *
 * Backends render through the same runtime outlet; frontends duplicate no concatenation logic. Previews are read-only and never generate.
 */
export function PromptPreviewButton<T extends RenderedPromptPreview>({
  title,
  load,
  notice,
  renderExtra,
  disabled,
  beforeOpen,
  saveFirst,
}: PromptPreviewButtonProps<T>) {
  const { t } = useTranslation("dashboard");
  const [preparing, setPreparing] = useState(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // New owners rotate controllers: new loads invalidate old ones, preventing previous owners from writing shared state.
  const inflight = useRef<AbortController | null>(null);
  const fetchPreview = useCallback(async (prepared?: AbortController) => {
    if (!prepared) inflight.current?.abort();
    const controller = prepared ?? new AbortController();
    inflight.current = controller;
    const { signal } = controller;
    setLoading(true);
    setError(null);
    try {
      const next = await load(signal);
      if (signal.aborted || inflight.current !== controller) return;
      setResult(next);
    } catch (e) {
      if (signal.aborted || inflight.current !== controller) return;
      setError(errMsg(e));
    } finally {
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- Finally already resets; only requests still owning inflight reset it, while superseded requests leave cleanup to successors
      if (!signal.aborted && inflight.current === controller) setLoading(false);
    }
  }, [load]);

  // Invalidate pending requests on unmount: cleanup never writes state, only cutting off superseded writers.
  useEffect(() => () => inflight.current?.abort(), []);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleOpen = async () => {
    if (preparing) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setPreparing(true);
    try {
      if (beforeOpen && !(await beforeOpen())) return;
      if (controller.signal.aborted || inflight.current !== controller) return;
      setOpen(true);
      await fetchPreview(controller);
    } finally {
      if (!controller.signal.aborted && inflight.current === controller) setPreparing(false);
    }
  };

  // Refetch every opening: discard results on closing, preventing stale text flashing on next opening.
  const handleClose = () => {
    inflight.current?.abort();
    inflight.current = null;
    setOpen(false);
    setLoading(false);
    setPreparing(false);
    setResult(null);
    setError(null);
    setCopied(false);
  };

  // Show Copied only on success: insecure contexts use execCommand fallback, and failed fallbacks never falsely report success.
  const handleCopy = (text: string) => {
    void copyText(text).then(
      () => setCopied(true),
      () => undefined,
    );
  };

  return (
    <>
      <Button variant="ghost" size="xs" onClick={() => void handleOpen()} disabled={disabled || preparing} title={title}>
        <Eye aria-hidden data-icon="inline-start" />
        {t(saveFirst ? "prompt_preview_save_first" : "prompt_preview_open")}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) handleClose();
        }}
      >
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {notice ? <DialogDescription>{notice}</DialogDescription> : null}
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-3">
              {loading && !result ? (
                <p className="text-sm text-muted-foreground">{t("prompt_preview_loading")}</p>
              ) : null}
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
              {result?.unavailable ? <p className="text-sm text-muted-foreground">{result.unavailable}</p> : null}
              {result?.warnings?.length ? (
                <ul
                  aria-label={t("prompt_preview_warnings_label")}
                  className="flex flex-col gap-1 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn"
                >
                  {result.warnings.map((warning, index) => (
                    // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Read-only prompt text in one preview may duplicate and lacks IDs; entries are stateless and entire results replace together
                    <li key={`prompt-preview-warning-${index}`}>{warning}</li>
                  ))}
                </ul>
              ) : null}
              {result?.text ? (
                <pre className="rounded-md border bg-card p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-subtle-foreground wrap-break-word">
                  {result.text}
                </pre>
              ) : null}
              {result && renderExtra ? renderExtra(result) : null}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => void fetchPreview()} disabled={loading}>
              <RefreshCw aria-hidden data-icon="inline-start" className={cn(loading && "animate-spin")} />
              {t("prompt_preview_refresh")}
            </Button>
            {result?.text ? (
              <Button onClick={() => handleCopy(result.text ?? "")}>
                {copied ? <Check aria-hidden data-icon="inline-start" /> : <Copy aria-hidden data-icon="inline-start" />}
                {copied ? t("message_copied") : t("prompt_preview_copy")}
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
