import { AlertTriangle, ShieldAlert, Sparkles, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { ArchiveDiagnostic } from "@/types";
import type { DiagnosticSeverity } from "@/utils/severity-tone";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

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

const SEVERITY_STYLE: Record<DiagnosticSeverity, { icon: LucideIcon; tone: string }> = {
  blocking: { icon: ShieldAlert, tone: "text-destructive" },
  auto_fixed: { icon: Sparkles, tone: "text-primary" },
  warnings: { icon: AlertTriangle, tone: "text-warn" },
};

/** 项目导入、导出的诊断清单：按严重程度分组列出问题与出处，没有任何条目时不显示。 */
export function ArchiveDiagnosticsDialog({
  title,
  description,
  sections,
  onClose,
}: ArchiveDiagnosticsDialogProps) {
  const { t } = useTranslation("common");
  const visibleSections = sections.filter((s) => s.items.length > 0);

  if (visibleSections.length === 0) return null;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-5">
            {visibleSections.map((section) => {
              const { icon: Icon, tone } = SEVERITY_STYLE[section.severity];
              return (
                <section key={section.key} className="flex flex-col gap-2">
                  <h3 className={cn("flex items-center gap-2 text-sm font-medium", tone)}>
                    <Icon aria-hidden className="size-4" />
                    {section.title}
                    <span className="font-mono text-xs text-muted-foreground tabular-nums">
                      {section.items.length}
                    </span>
                  </h3>
                  <ul className="flex flex-col gap-1.5">
                    {section.items.map((item, index) => (
                      <li
                        key={`${section.key}-${item.code}-${item.location ?? index}`}
                        className="flex min-w-0 flex-col gap-1 rounded-md border bg-card px-3 py-2 text-sm text-subtle-foreground"
                      >
                        <p className="wrap-break-word">{item.message}</p>
                        {item.location && (
                          <TruncatedText text={item.location} className="font-mono text-xs text-muted-foreground" />
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button />}>{t("close")}</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
