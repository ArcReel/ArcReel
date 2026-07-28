/**
 * OpenClaw 集成引导 Modal
 * 提示词区域（可复制，含动态 skill.md URL）、3 步使用说明、"获取 API 令牌"按钮
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { copyText } from "@/utils/clipboard";
import { Check, Copy, ExternalLink, X } from "lucide-react";
import { useLocation } from "wouter";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  DROPDOWN_PANEL_STYLE,
  GHOST_BTN_CLS,
  GHOST_BTN_LG_CLS,
  ICON_BTN_FILLED_CLS,
} from "@/components/ui/darkroom-tokens";

function LobsterIcon({ className }: { className?: string }) {
  return (
    <span className={className} aria-hidden="true" role="img">
      🦞
    </span>
  );
}

interface OpenClawModalProps {
  onClose: () => void;
}

const STEP_KEYS = [
  { step: "01", titleKey: "openclaw_step_01_title", descKey: "openclaw_step_01_desc" },
  { step: "02", titleKey: "openclaw_step_02_title", descKey: "openclaw_step_02_desc" },
  { step: "03", titleKey: "openclaw_step_03_title", descKey: "openclaw_step_03_desc" },
] as const;

const SKILL_URL = `${window.location.origin}/skill.md`;

export function OpenClawModal({ onClose }: OpenClawModalProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, navigate] = useLocation();
  const [copied, setCopied] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const copiedTimerRef = useRef<number | null>(null);

  const systemPrompt = t("dashboard:openclaw_system_prompt", { skillUrl: SKILL_URL });

  const handleCopyPrompt = useCallback(async () => {
    await copyText(systemPrompt);
    setCopied(true);
    if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current);
    copiedTimerRef.current = window.setTimeout(() => {
      copiedTimerRef.current = null;
      setCopied(false);
    }, 2000);
  }, [systemPrompt]);

  useEffect(() => () => {
    if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current);
  }, []);

  const handleGoToApiKeys = useCallback(() => {
    onClose();
    navigate("/app/settings?section=api-keys");
  }, [navigate, onClose]);

  useEscapeClose(onClose);
  useFocusTrap(dialogRef, true);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-8 backdrop-blur-sm"
    >
      {/* Pointer-only backdrop. Esc 通过 useEscapeClose 关闭，避免引入可聚焦元素破坏 focus trap。 */}
      <div
        aria-hidden="true"
        className="absolute inset-0"
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        className="relative z-10 flex max-h-[90vh] w-full max-w-lg flex-col overflow-y-auto rounded-2xl border border-hairline shadow-2xl shadow-black/60"
        style={DROPDOWN_PANEL_STYLE}
      >
        <div
          className="sticky top-0 z-10 flex items-center justify-between border-b border-hairline px-5 py-4"
          style={DROPDOWN_PANEL_STYLE}
        >
          <div className="flex items-center gap-2.5">
            <LobsterIcon className="text-xl leading-none" />
            <div>
              <h2 className="text-[14px] font-semibold text-text">{t("dashboard:openclaw_title")}</h2>
              <p className="text-[12px] text-text-4">{t("dashboard:openclaw_subtitle")}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className={ICON_BTN_FILLED_CLS}
            aria-label={t("common:close")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-5 p-5">
          {/* Prompt */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
                {t("dashboard:openclaw_prompt_label")}
              </span>
              <button
                type="button"
                onClick={() => void handleCopyPrompt()}
                className={GHOST_BTN_CLS}
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3 text-good" />
                    {t("common:copied")}
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" />
                    {t("common:copy")}
                  </>
                )}
              </button>
            </div>
            <div className="rounded-xl border border-accent/30 bg-bg p-3">
              <pre className="whitespace-pre-wrap font-mono text-[12px] leading-5 text-accent-2">
                {systemPrompt}
              </pre>
            </div>
            <p className="mt-1.5 text-[11px] text-text-4">
              {t("dashboard:openclaw_skill_url_label")}
              <a
                href={SKILL_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-1 inline-flex items-center gap-0.5 text-accent-2 hover:text-accent"
              >
                {SKILL_URL}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>

          {/* Steps */}
          <div>
            <div className="mb-3 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
              {t("dashboard:openclaw_steps_label")}
            </div>
            <div className="space-y-2">
              {STEP_KEYS.map(({ step, titleKey, descKey }) => (
                <div
                  key={step}
                  className="flex gap-3 rounded-xl border border-hairline-soft bg-bg-grad-a/40 px-3.5 py-3"
                >
                  <div className="shrink-0 pt-0.5 font-mono text-[11px] font-bold tracking-[0.14em] text-accent/70">
                    {step}
                  </div>
                  <div>
                    <div className="text-[12px] font-semibold text-text-2">
                      {t(`dashboard:${titleKey}`)}
                    </div>
                    <div className="mt-0.5 text-[12px] leading-[1.55] text-text-4">
                      {t(`dashboard:${descKey}`)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className={`${GHOST_BTN_LG_CLS} flex-1 justify-center`}
            >
              {t("common:close")}
            </button>
            <button
              type="button"
              onClick={handleGoToApiKeys}
              className={`${ACCENT_BTN_CLS} flex-1 justify-center`}
              style={ACCENT_BUTTON_STYLE}
            >
              {t("dashboard:openclaw_get_api_token")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
