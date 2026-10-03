import { useId } from "react";
import { AlertTriangle, ShieldAlert, Sparkles, Info } from "lucide-react";
import type { ArchiveDiagnostic } from "@/types";
import { GlassModal } from "@/components/legacy/GlassModal";
import { ModalCloseButton } from "@/components/legacy/ModalCloseButton";
import {
  SEVERITY_TONES,
  WARM_TONE,
  type DiagnosticSeverity,
} from "@/utils/severity-tone";

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
  const visibleSections = sections.filter((s) => s.items.length > 0);
  const hasContent = visibleSections.length > 0;
  const titleId = useId();

  if (!hasContent) return null;

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-full max-w-2xl"
      hairlineTone="warm"
    >
      <div
        className="flex items-start justify-between gap-4 px-6 py-5"
        style={{ borderBottom: "1px solid color-mix(in oklab, var(--border) 50%, transparent)" }}
      >
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in oklab, var(--warn) 15%, transparent), color-mix(in oklab, var(--warn) 5%, transparent))",
              border: `1px solid ${WARM_TONE.ring}`,
              color: WARM_TONE.color,
              boxShadow: `0 8px 18px -8px ${WARM_TONE.glow}`,
            }}
          >
            <AlertTriangle className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <h2
              id={titleId}
              className="display-serif text-[17px] font-semibold tracking-tight"
              style={{ color: "var(--foreground)" }}
            >
              {title}
            </h2>
            <p
              className="mt-1 text-[12.5px] leading-[1.55]"
              style={{ color: "var(--muted-foreground)" }}
            >
              {description}
            </p>
          </div>
        </div>
        <ModalCloseButton onClick={onClose} />
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
                  style={{ color: "var(--muted-foreground)" }}
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
                      border: "1px solid color-mix(in oklab, var(--border) 50%, transparent)",
                      color: "var(--subtle-foreground)",
                    }}
                  >
                    <p>{item.message}</p>
                    {item.location && (
                      <p
                        className="num mt-1 text-[11px]"
                        style={{ color: "var(--muted-foreground)" }}
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
    </GlassModal>
  );
}
