import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ShieldAlert, Sparkles, Info, X } from "lucide-react";
import type { ArchiveDiagnostic } from "@/types";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import {
  SEVERITY_TONES,
  WARM_TONE,
  type DiagnosticSeverity,
} from "@/utils/severity-tone";

export type { DiagnosticSeverity };

interface DiagnosticsSection {
  key: string;
  title: string;
  severity: DiagnosticSeverity;
  items: ArchiveDiagnostic[];
}

interface ArchiveDiagnosticsDialogProps {
  title: string;
  description: string;
  sections: DiagnosticsSection[];
  onClose: () => void;
}

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

const SEVERITY_ICONS: Record<DiagnosticSeverity, typeof Info> = {
  blocking: ShieldAlert,
  auto_fixed: Sparkles,
  warnings: AlertTriangle,
};

export function ArchiveDiagnosticsDialog({
  title,
  description,
  sections,
  onClose,
}: ArchiveDiagnosticsDialogProps) {
  const { t } = useTranslation("common");
  const visibleSections = sections.filter((s) => s.items.length > 0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const hasContent = visibleSections.length > 0;
  useEscapeClose(onClose, hasContent);
  useFocusTrap(dialogRef, hasContent);

  if (!hasContent) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{
        background: "oklch(0 0 0 / 0.65)",
        backdropFilter: "blur(2px)",
        WebkitBackdropFilter: "blur(2px)",
      }}
    >
      <button
        type="button"
        aria-label={t("close")}
        onClick={onClose}
        className="absolute inset-0"
        style={{ background: "transparent" }}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="archive-diagnostics-title"
        className="relative w-full max-w-2xl overflow-hidden rounded-2xl"
        style={{
          background: PANEL_BG,
          border: "1px solid var(--color-hairline)",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          boxShadow:
            "0 24px 60px -12px oklch(0 0 0 / 0.6), inset 0 1px 0 oklch(1 0 0 / 0.05)",
        }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, var(--color-warm-fade), transparent)",
          }}
        />

        <div
          className="flex items-start justify-between gap-4 px-6 py-5"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <div className="flex items-start gap-3">
            <span
              aria-hidden
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-warm-tint), var(--color-warm-tint-faint))",
                border: `1px solid ${WARM_TONE.ring}`,
                color: WARM_TONE.color,
                boxShadow: `0 8px 18px -8px ${WARM_TONE.glow}`,
              }}
            >
              <AlertTriangle className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <h2
                id="archive-diagnostics-title"
                className="display-serif text-[17px] font-semibold tracking-tight"
                style={{ color: "var(--color-text)" }}
              >
                {title}
              </h2>
              <p
                className="mt-1 text-[12.5px] leading-[1.55]"
                style={{ color: "var(--color-text-3)" }}
              >
                {description}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className="focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--color-text)";
              e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-3)";
              e.currentTarget.style.background = "transparent";
            }}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[60vh] space-y-3 overflow-y-auto px-6 py-5">
          {visibleSections.map((section) => {
            const tone = SEVERITY_TONES[section.severity];
            const ToneIcon = SEVERITY_ICONS[section.severity];
            return (
              <section
                key={section.key}
                className="rounded-xl px-4 py-3"
                style={{
                  background: tone.soft,
                  border: `1px solid ${tone.ring}`,
                  boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.03)",
                }}
              >
                <div className="mb-2.5 flex items-center gap-2">
                  <span
                    aria-hidden
                    className="grid h-6 w-6 place-items-center rounded-md"
                    style={{
                      background: "oklch(0.16 0.010 265 / 0.6)",
                      border: `1px solid ${tone.ring}`,
                      color: tone.color,
                    }}
                  >
                    <ToneIcon className="h-3 w-3" />
                  </span>
                  <h3
                    className="text-[12.5px] font-semibold tracking-tight"
                    style={{ color: tone.color }}
                  >
                    {section.title}
                  </h3>
                  <span
                    className="num text-[10px]"
                    style={{ color: "var(--color-text-4)" }}
                  >
                    {section.items.length}
                  </span>
                </div>
                <ul className="space-y-1.5 text-[12.5px] leading-[1.55]">
                  {section.items.map((item, index) => (
                    <li
                      key={`${section.key}-${item.code}-${item.location ?? index}`}
                      className="rounded-lg px-3 py-2"
                      style={{
                        background: "oklch(0.16 0.010 265 / 0.5)",
                        border: "1px solid var(--color-hairline-soft)",
                        color: "var(--color-text-2)",
                      }}
                    >
                      <p>{item.message}</p>
                      {item.location && (
                        <p
                          className="num mt-1 text-[11px]"
                          style={{ color: "var(--color-text-4)" }}
                        >
                          {item.location}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
