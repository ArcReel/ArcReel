import { useCallback, useId, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { DurationTierPicker } from "@/components/shared/DurationTierPicker";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Textarea } from "@/components/ui/textarea";
import { useProjectsStore } from "@/stores/projects-store";

interface BriefFields {
  brief: string;
  /** 目标总时长（秒）；「自定义」里填的不是正整数时为 null，不能保存。 */
  targetDuration: number | null;
}

/**
 * 广告项目概览常驻的「创作灵感」区：灵感正文与目标总时长是一个编辑单元，AI 生成脚本按它们规划。
 * 新建的广告项目在这里完成首次录入，不经单独的初始化页。
 */
export function AdBrief({
  projectName,
  brief,
  targetDuration,
  readOnly,
}: {
  projectName: string;
  brief: string | undefined;
  targetDuration: number | undefined;
  readOnly: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const headingId = useId();
  const fieldId = useId();

  const source = useMemo<BriefFields>(
    () => ({ brief: brief ?? "", targetDuration: targetDuration ?? null }),
    [brief, targetDuration],
  );
  const save = useCallback(
    async (value: BriefFields, saved: BriefFields) => {
      if (value.targetDuration === null) throw new Error(t("ad_target_duration_invalid"));
      // 只提交改动过的字段：另一处（如项目设置）同时改了目标总时长时，只改灵感不会把它写回旧值
      const patch: { brief?: string; target_duration?: number } = {};
      const nextBrief = value.brief.trim();
      if (nextBrief !== saved.brief) patch.brief = nextBrief;
      if (value.targetDuration !== saved.targetDuration) patch.target_duration = value.targetDuration;
      if (Object.keys(patch).length > 0) {
        await API.updateProject(projectName, patch);
        await useProjectsStore.getState().refreshProject(projectName);
      }
      return { brief: nextBrief, targetDuration: value.targetDuration };
    },
    [projectName, t],
  );
  const unit = useEditUnit({ source, save });

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h2 id={headingId} className="flex min-h-7 items-center text-sm font-medium text-subtle-foreground">
        {t("ad_brief_title")}
      </h2>
      {readOnly ? (
        <div className="flex max-w-[40em] flex-col gap-2">
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-subtle-foreground">
            {unit.savedValue.brief || t("ad_brief_empty")}
          </p>
          {unit.savedValue.targetDuration !== null ? (
            <p className="num text-sm text-muted-foreground">
              {t("ad_brief_target_duration", { value: unit.savedValue.targetDuration })}
            </p>
          ) : null}
        </div>
      ) : (
        <div className="flex max-w-[40em] flex-col gap-4">
          <div className="-mx-2.5">
            <Textarea
              id={fieldId}
              aria-labelledby={headingId}
              variant="plain"
              value={unit.value.brief}
              onChange={(event) => unit.setValue((prev) => ({ ...prev, brief: event.target.value }))}
              placeholder={t("ad_brief_placeholder")}
              className="max-h-none"
            />
          </div>
          <DurationTierPicker
            value={unit.value.targetDuration}
            onChange={(next) => unit.setValue((prev) => ({ ...prev, targetDuration: next }))}
          />
        </div>
      )}
      {readOnly ? null : <UnsavedChangesBar unit={unit} className="max-w-[40em]" />}
    </section>
  );
}
