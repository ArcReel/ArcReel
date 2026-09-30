import { useTranslation } from "react-i18next";
import { InsertShotButton, type InsertShotHandler } from "./ShotStructureActions";

/**
 * 正式脚本里一条分镜都没有时的空状态：说明现状，并给出「新增第一个分镜」。
 * 不给新增入口（演示态等只读场景）时只显示说明。
 */
export function EmptyScriptState({
  contentMode,
  onInsert,
}: {
  contentMode: "narration" | "drama" | "ad";
  onInsert?: InsertShotHandler;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-[13px]" style={{ color: "var(--color-text-4)" }}>
      <p className="m-0">{t("timeline_empty_script_hint")}</p>
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
