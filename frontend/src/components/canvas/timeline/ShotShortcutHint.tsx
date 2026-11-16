import { useTranslation } from "react-i18next";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { useMobileTier } from "@/hooks/useMobileTier";
import { isApplePlatform } from "./useShotShortcuts";

/**
 * 分镜列表底部常驻的快捷键提示。只读展示（不能保存）时不提保存。
 *
 * 移动档（视口窄于 768px）不渲染：触屏没有物理键盘，这条提示只占高度。
 */
export function ShotShortcutHint({ canSave }: { canSave: boolean }) {
  const { t } = useTranslation("dashboard");
  const mobile = useMobileTier();
  const modifier = isApplePlatform() ? "⌘" : "Ctrl";
  if (mobile) return null;
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
