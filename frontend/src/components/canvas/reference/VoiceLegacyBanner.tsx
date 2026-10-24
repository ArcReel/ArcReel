import { History, X } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 存量过渡横幅：角色参考音频更新后，提示本画布有片段生成于设置之前；计数与关闭态写回的口径见 `voice-legacy-notice.ts`。 */
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
