/**
 * OpenClaw Integration Guide Modal
 * Prompt area (copyable, with dynamic skill.md URL), 3-step instructions, "Get API Token" button
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { copyText } from "@/utils/clipboard";
import { Check, Copy, ExternalLink, X } from "lucide-react";
import { useLocation } from "wouter";

// 🦞 SVG lobster icon (inline, no external dep)
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

export function OpenClawModal({ onClose }: OpenClawModalProps) {
  const { t } = useTranslation("dashboard");
  const [, navigate] = useLocation();
  const [copied, setCopied] = useState(false);

  // Dynamic skill.md URL based on current origin
  const skillUrl = useMemo(
    () => `${window.location.origin}/skill.md`,
    [],
  );

  const systemPrompt = useMemo(
    () => t("openclaw_system_prompt", { skillUrl }),
    [skillUrl, t],
  );

  // Steps data — localized
  const steps = useMemo(() => [
    {
      step: "01",
      title: t("openclaw_step1_title"),
      desc: t("openclaw_step1_desc"),
    },
    {
      step: "02",
      title: t("openclaw_step2_title"),
      desc: t("openclaw_step2_desc"),
    },
    {
      step: "03",
      title: t("openclaw_step3_title"),
      desc: t("openclaw_step3_desc"),
    },
  ], [t]);

  const handleCopyPrompt = useCallback(async () => {
    await copyText(systemPrompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [systemPrompt]);

  // Navigate to API Key management page
  const handleGoToApiKeys = useCallback(() => {
    onClose();
    navigate("/app/settings?section=api-keys");
  }, [navigate, onClose]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-8"
    >
      {/* backdrop: click-to-close */}
      <button
        type="button"
        aria-label={t("openclaw_close")}
        className="absolute inset-0 cursor-default appearance-none border-0 bg-transparent p-0"
        onClick={onClose}
      />
      <div className="relative z-10 flex w-full max-w-lg flex-col rounded-2xl border border-gray-800 bg-gray-900 shadow-2xl shadow-black/60 max-h-[90vh] overflow-y-auto">
        {/* ——— Header ——— */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-800 bg-gray-900 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <LobsterIcon className="text-xl leading-none" />
            <div>
              <h2 className="text-sm font-semibold text-gray-100">{t("openclaw_title")}</h2>
              <p className="text-xs text-gray-500">{t("openclaw_subtitle")}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
            aria-label={t("openclaw_close")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* ——— Prompt area ——— */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-gray-400">{t("openclaw_prompt_label")}</span>
              <button
                type="button"
                onClick={() => void handleCopyPrompt()}
                className="inline-flex items-center gap-1 rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300 transition-colors hover:border-gray-600 hover:bg-gray-700"
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3 text-emerald-400" />
                    {t("openclaw_copied")}
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" />
                    {t("openclaw_copy")}
                  </>
                )}
              </button>
            </div>
            <div className="rounded-xl border border-indigo-500/20 bg-gray-950 p-3">
              <pre className="whitespace-pre-wrap font-mono text-xs leading-5 text-indigo-200">
                {systemPrompt}
              </pre>
            </div>
            <p className="mt-1.5 text-xs text-gray-600">
              {t("openclaw_skill_doc_url")}
              <a
                href={skillUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-1 inline-flex items-center gap-0.5 text-indigo-400 hover:text-indigo-300"
              >
                {skillUrl}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>

          {/* ——— 3 Steps ——— */}
          <div>
            <div className="mb-3 text-xs font-medium text-gray-400">{t("openclaw_steps_title")}</div>
            <div className="space-y-2">
              {steps.map(({ step, title, desc }) => (
                <div
                  key={step}
                  className="flex gap-3 rounded-xl border border-gray-800 bg-gray-950/50 px-3.5 py-3"
                >
                  <div className="flex-shrink-0 font-mono text-xs font-bold text-indigo-500/70 pt-0.5">
                    {step}
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-gray-200">{title}</div>
                    <div className="mt-0.5 text-xs leading-4.5 text-gray-500">{desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ——— Action buttons ——— */}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-300 transition-colors hover:border-gray-600 hover:bg-gray-700"
            >
              {t("openclaw_close")}
            </button>
            <button
              type="button"
              onClick={handleGoToApiKeys}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
            >
              {t("openclaw_get_api_token")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
