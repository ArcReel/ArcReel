import type { TFunction } from "i18next";
import { FileX, Wrench, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import type { WorkflowMigrationAdjustment, WorkflowMigrationReport } from "@/types/workflow";
import { episodeDisplayName, type EpisodeLedger } from "@/utils/episode-display";

const ASSET_TYPES = new Set(["character", "scene", "prop", "product"]);
const ARTIFACT_KINDS = new Set([
  "asset-sheet",
  "episode-script-plan",
  "episode-script",
  "episode-grid",
  "episode-storyboard",
  "episode-video",
  "episode-audio",
  "episode-subtitle",
  "episode-presentation",
  "episode-final-cut",
  "episode-jianying-draft",
  "project-bgm",
]);

function assetTypeLabel(t: TFunction, assetType: string | undefined): string {
  return ASSET_TYPES.has(assetType ?? "")
    ? t(`dashboard:migration_asset_type_${assetType}`)
    : t("dashboard:migration_artifact_kind_other");
}

function adjustmentText(t: TFunction, item: WorkflowMigrationAdjustment, episodes: EpisodeLedger): string {
  const episode = item.episode === null ? "" : episodeDisplayName(episodes, item.episode, t);
  const params = item.params;
  switch (item.code) {
    case "draft_name_conflict_resolved":
      return t("dashboard:migration_adjustment_draft_name_conflict_resolved", { episode });
    case "duplicate_episode_entry_removed":
      return t("dashboard:migration_adjustment_duplicate_episode_entry_removed", { episode, file: item.file ?? "" });
    case "shared_script_binding_split":
      return t("dashboard:migration_adjustment_shared_script_binding_split", {
        episode,
        owner: params.owner ? episodeDisplayName(episodes, Number(params.owner), t) : "",
        from: params.from ?? "",
        to: params.to ?? "",
      });
    case "script_episode_corrected":
      return params.from
        ? t("dashboard:migration_adjustment_script_episode_corrected", { episode, file: item.file ?? "" })
        : t("dashboard:migration_adjustment_script_episode_corrected_missing", { episode, file: item.file ?? "" });
    case "script_binding_canonicalized":
      return t("dashboard:migration_adjustment_script_binding_canonicalized", {
        episode,
        from: params.from ?? "",
        to: params.to ?? "",
      });
    case "item_id_reassigned":
      return params.from
        ? t("dashboard:migration_adjustment_item_id_reassigned_duplicate", { episode, from: params.from, to: params.to })
        : t("dashboard:migration_adjustment_item_id_reassigned_missing", { episode, to: params.to });
    case "duplicate_asset_removed":
      return params.kept
        ? t("dashboard:migration_adjustment_duplicate_asset_removed", {
            assetType: assetTypeLabel(t, params.asset_type),
            name: params.name,
            kept: params.kept,
          })
        : t("dashboard:migration_adjustment_blank_asset_removed", { assetType: assetTypeLabel(t, params.asset_type) });
    case "duplicate_asset_derivative_merged":
      return t(
        params.folded
          ? "dashboard:migration_adjustment_duplicate_asset_derivative_folded"
          : "dashboard:migration_adjustment_duplicate_asset_derivative_moved",
        { name: params.name, kept: params.kept, derivative: params.derivative, into: params.into },
      );
    case "duplicate_asset_references_rewritten":
      return t("dashboard:migration_adjustment_duplicate_asset_references_rewritten", {
        episode,
        assetType: assetTypeLabel(t, params.asset_type),
        name: params.name,
        kept: params.kept,
      });
    case "shared_reference_file_copied":
      return t("dashboard:migration_adjustment_shared_reference_file_copied", {
        assetType: assetTypeLabel(t, params.asset_type),
        name: params.name,
        owner: params.owner,
        from: params.from ?? "",
        file: item.file ?? "",
      });
    case "placeholder_asset_added":
      return t("dashboard:migration_adjustment_placeholder_asset_added", {
        assetType: assetTypeLabel(t, params.asset_type),
        name: params.name,
      });
  }
}

function Section({
  icon: Icon,
  tone,
  title,
  count,
  hint,
  children,
}: {
  icon: LucideIcon;
  tone: string;
  title: string;
  count: number;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className={cn("flex items-center gap-2 text-sm font-medium", tone)}>
        <Icon aria-hidden className="size-4" />
        {title}
        <span className="font-mono text-xs text-muted-foreground tabular-nums">{count}</span>
      </h3>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      <ul className="flex flex-col gap-1.5">{children}</ul>
    </section>
  );
}

const ITEM = "flex min-w-0 flex-col gap-1 rounded-md border bg-card px-3 py-2 text-sm text-subtle-foreground";

/**
 * 迁移报告的只读内容：「已调整」列出规整的取舍、补的占位与修复的结构，「未能带入」列出原地保留、
 * 没有登记的文件。项目页的升级提示与导入前确认窗共用。
 */
export function MigrationReportContent({
  report,
  episodes,
}: {
  report: WorkflowMigrationReport;
  episodes: EpisodeLedger;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-5">
      {report.adjusted.length > 0 && (
        <Section icon={Wrench} tone="text-primary" title={t("migration_report_adjusted")} count={report.adjusted.length}>
          {report.adjusted.map((item, index) => (
            // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 迁移报告的只读列表，同一代码与文件可出现多条且无唯一标识；条目不带状态，界面不重排也不筛选
            <li key={`${item.code}-${item.file ?? ""}-${index}`} className={ITEM}>
              <p className="wrap-break-word">{adjustmentText(t, item, episodes)}</p>
              {item.backup && (
                <TruncatedText
                  text={t("migration_report_backup", { path: item.backup })}
                  className="font-mono text-xs text-muted-foreground"
                />
              )}
            </li>
          ))}
        </Section>
      )}
      {report.skipped.length > 0 && (
        <Section
          icon={FileX}
          tone="text-warn"
          title={t("migration_report_skipped")}
          count={report.skipped.length}
          hint={t("migration_report_skipped_hint")}
        >
          {report.skipped.map((item) => (
            <li key={`${item.kind}-${item.episode ?? ""}-${item.resource_id}`} className={ITEM}>
              <p className="wrap-break-word">
                {ARTIFACT_KINDS.has(item.kind)
                  ? t(`migration_artifact_kind_${item.kind}`)
                  : t("migration_artifact_kind_other")}
                {item.episode !== null ? ` · ${episodeDisplayName(episodes, item.episode, t)}` : ""}
              </p>
              <TruncatedText text={item.artifact_path} className="font-mono text-xs text-muted-foreground" />
            </li>
          ))}
        </Section>
      )}
    </div>
  );
}
