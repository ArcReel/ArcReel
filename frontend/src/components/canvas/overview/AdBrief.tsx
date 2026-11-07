import { useCallback, useId, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { DurationTierPicker } from "@/components/shared/DurationTierPicker";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Textarea } from "@/components/ui/textarea";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";

interface BriefFields {
  brief: string;
  /** Target total duration in seconds; invalid nonpositive/noninteger Custom input becomes null and cannot be saved. */
  targetDuration: number | null;
}

/**
 * Persistent Creative inspiration section in ad-project overviews: inspiration body and target duration form one editing unit used by AI script planning.
 * New ad projects enter initial content here without a separate initialization page.
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
      // Submit only changed fields: if another view such as project settings changes target duration concurrently, editing inspiration alone must not restore its old value
      const patch: { brief?: string; target_duration?: number } = {};
      const nextBrief = value.brief.trim();
      if (nextBrief !== saved.brief) patch.brief = nextBrief;
      if (value.targetDuration !== saved.targetDuration) patch.target_duration = value.targetDuration;
      if (Object.keys(patch).length > 0) {
        await API.updateProject(projectName, patch);
        await refreshAfterWrite(projectName, t);
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
