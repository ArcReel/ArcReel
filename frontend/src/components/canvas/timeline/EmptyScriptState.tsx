import { useTranslation } from "react-i18next";
import { InsertShotButton, type InsertShotHandler } from "./ShotStructureActions";

export function EmptyScriptState({
  contentMode,
  onInsert,
}: {
  contentMode: "narration" | "drama" | "ad";
  onInsert?: InsertShotHandler;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
      <p>{t("timeline_empty_script_hint")}</p>
      {onInsert && (
        <InsertShotButton
          afterId={null}
          contentMode={contentMode}
          onInsert={onInsert}
          label={t("shot_add_first")}
          variant="primary"
        />
      )}
    </div>
  );
}
