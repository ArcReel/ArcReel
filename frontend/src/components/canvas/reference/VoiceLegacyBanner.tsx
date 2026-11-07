import { History, X } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Legacy transition banner: after character reference audio changes, flag clips generated before that setting; see `voice-legacy-notice.ts` for count and dismissal-write rules. */
export function VoiceLegacyBanner({
  onDismiss,
  dismissLabel,
  message,
}: {
  onDismiss: () => void;
  dismissLabel: string;
  message: string;
}) {
  return (
    <div className="flex items-start gap-2.5 border-b border-warn/30 bg-warn/10 px-5 py-2">
      <History className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true" />
      <p className="flex-1 text-xs leading-relaxed text-subtle-foreground">{message}</p>
      <Button variant="ghost" size="icon-xs" onClick={onDismiss} aria-label={dismissLabel}>
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}
