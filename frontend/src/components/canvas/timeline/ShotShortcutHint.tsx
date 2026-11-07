import { useTranslation } from "react-i18next";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { isApplePlatform } from "./useShotShortcuts";

/** Persistent shortcut hint below the storyboard list; omit saving in read-only display where saving is unavailable. */
export function ShotShortcutHint({ canSave }: { canSave: boolean }) {
  const { t } = useTranslation("dashboard");
  const modifier = isApplePlatform() ? "⌘" : "Ctrl";
  return (
    <p className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/50 px-3 py-2 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <KbdGroup>
          <Kbd>J</Kbd>
          <Kbd>K</Kbd>
        </KbdGroup>
        {t("shot_shortcut_switch")}
      </span>
      {canSave ? (
        <span className="inline-flex items-center gap-1.5">
          <KbdGroup>
            <Kbd>{modifier}</Kbd>
            <Kbd>S</Kbd>
          </KbdGroup>
          {t("shot_shortcut_save")}
        </span>
      ) : null}
    </p>
  );
}
