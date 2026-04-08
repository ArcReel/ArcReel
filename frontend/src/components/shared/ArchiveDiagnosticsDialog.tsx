import { AlertTriangle } from "lucide-react";
import type { ArchiveDiagnostic } from "@/types";

interface DiagnosticsSection {
  key: string;
  title: string;
  tone: string;
  items: ArchiveDiagnostic[];
}

interface ArchiveDiagnosticsDialogProps {
  title: string;
  description: string;
  sections: DiagnosticsSection[];
  onClose: () => void;
}

export function ArchiveDiagnosticsDialog({
  title,
  description,
  sections,
  onClose,
}: ArchiveDiagnosticsDialogProps) {
  const visibleSections = sections.filter((s) => s.items.length > 0);

  if (visibleSections.length === 0) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4">
      <div className="w-full max-w-2xl rounded-2xl border border-gray-800 bg-gray-900 p-6 shadow-2xl shadow-black/40">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="rounded-full bg-amber-400/10 p-2 text-amber-300">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <h2 className="text-lg font-semibold text-gray-100">{title}</h2>
            </div>
            <p className="text-sm leading-6 text-gray-400">{description}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:border-gray-500 hover:text-white"
          >
            Close
          </button>
        </div>

        <div className="mt-5 max-h-[60vh] space-y-4 overflow-y-auto pr-1">
          {visibleSections.map((section) => (
            <section key={section.key} className={`rounded-xl border p-4 ${section.tone}`}>
              <h3 className="text-sm font-semibold">{section.title}</h3>
              <ul className="mt-3 space-y-2 text-sm leading-6">
                {section.items.map((item, index) => (
                  <li
                    key={`${section.key}-${item.code}-${item.location ?? index}`}
                    className="rounded-lg bg-black/15 px-3 py-2"
                  >
                    <p>{item.message}</p>
                    {item.location && (
                      <p className="mt-1 font-mono text-xs text-current/70">{item.location}</p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
