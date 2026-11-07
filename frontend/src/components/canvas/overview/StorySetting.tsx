import { useCallback, useId, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import type { StoryGenerateError } from "@/stores/overview-generate-store";
import type { ProjectOverview } from "@/types";

type SettingFields = Pick<ProjectOverview, "synopsis" | "genre" | "theme" | "world_setting">;
type FieldKey = keyof SettingFields;

const FIELD_KEYS: FieldKey[] = ["synopsis", "genre", "theme", "world_setting"];

function fieldsOf(overview: ProjectOverview | undefined): SettingFields {
  return {
    synopsis: overview?.synopsis ?? "",
    genre: overview?.genre ?? "",
    theme: overview?.theme ?? "",
    world_setting: overview?.world_setting ?? "",
  };
}

interface StorySettingProps {
  projectName: string;
  overview: ProjectOverview | undefined;
  readOnly: boolean;
  /** Readable project sources (whole-source or existing settings) enable generate-from-source. */
  canGenerate: boolean;
  /** Generating from source shows skeletons/reading-source status, filling in place afterward. */
  generating: boolean;
  generateError: StoryGenerateError | null;
  onGenerate: () => void;
  /** One explanatory line beneath titles, e.g. saved-script destination in ad story-setting tabs. */
  description?: string;
  /** In-app navigation retaining this editing unit; see matching `useEditUnit` parameter. Require stable references. */
  allowNavigation?: (to: string) => boolean;
}

/**
 * Story settings: synopsis/genre/theme/world form one editing unit, styled as body text with an inline notice below after edits.
 * Regenerating from source replaces all four fields, requiring AlertDialog confirmation and discarding unsaved changes too.
 */
export function StorySetting({
  projectName,
  overview,
  readOnly,
  canGenerate,
  generating,
  generateError,
  onGenerate,
  description,
  allowNavigation,
}: StorySettingProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const headingId = useId();
  const fieldId = useId();
  const [confirming, setConfirming] = useState(false);

  const source = useMemo(() => fieldsOf(overview), [overview]);
  const save = useCallback(
    async (value: SettingFields) => {
      // Trim before persistence, avoiding whitespace-only storage.
      const trimmed: SettingFields = {
        synopsis: value.synopsis.trim(),
        genre: value.genre.trim(),
        theme: value.theme.trim(),
        world_setting: value.world_setting.trim(),
      };
      await API.updateOverview(projectName, trimmed);
      await refreshAfterWrite(projectName, t);
      return trimmed;
    },
    [projectName, t],
  );
  const unit = useEditUnit({ source, save, allowNavigation });

  const hasContent = FIELD_KEYS.some((key) => unit.savedValue[key] !== "");
  const needsConfirm = hasContent || unit.dirty;
  const generateLabel = unit.dirty
    ? t("overview_discard_and_regenerate")
    : hasContent
      ? t("overview_regenerate")
      : t("overview_generate");

  const startGenerate = () => {
    if (needsConfirm) {
      setConfirming(true);
      return;
    }
    onGenerate();
  };

  const confirmGenerate = () => {
    setConfirming(false);
    unit.discard();
    onGenerate();
  };

  const setField = (key: FieldKey) => (event: { target: { value: string } }) =>
    unit.setValue((prev) => ({ ...prev, [key]: event.target.value }));

  let body;
  if (generating) {
    body = (
      <div className="flex flex-col gap-3">
        <p role="status" className="flex items-center gap-2 text-sm text-subtle-foreground">
          <span aria-hidden className="size-1.5 shrink-0 animate-breath rounded-full bg-primary" />
          {t("overview_reading_source")}
        </p>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-11/12" />
        <Skeleton className="h-4 w-3/5" />
        <div className="flex gap-2 pt-1">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-28" />
        </div>
      </div>
    );
  } else if (readOnly) {
    body = (
      <dl className="flex max-w-[40em] flex-col gap-3">
        {FIELD_KEYS.filter((key) => unit.savedValue[key] !== "").map((key) => (
          <div key={key}>
            <dt className="text-xs font-medium text-muted-foreground">{t(`overview_field_${key}`)}</dt>
            <dd className="mt-1 text-sm leading-relaxed whitespace-pre-wrap text-subtle-foreground">
              {unit.savedValue[key]}
            </dd>
          </div>
        ))}
      </dl>
    );
  } else {
    body = (
      <div className="flex max-w-[40em] flex-col gap-3">
        <FieldBlock id={`${fieldId}-synopsis`} label={t("overview_field_synopsis")}>
          <Textarea
            id={`${fieldId}-synopsis`}
            variant="plain"
            value={unit.value.synopsis}
            onChange={setField("synopsis")}
            placeholder={t("overview_placeholder_synopsis")}
            className="max-h-none"
          />
        </FieldBlock>
        <div className="grid grid-cols-1 gap-3 @md/canvas:grid-cols-2">
          <FieldBlock id={`${fieldId}-genre`} label={t("overview_field_genre")}>
            <Input
              id={`${fieldId}-genre`}
              variant="plain"
              value={unit.value.genre}
              onChange={setField("genre")}
              placeholder={t("overview_placeholder_genre")}
            />
          </FieldBlock>
          <FieldBlock id={`${fieldId}-theme`} label={t("overview_field_theme")}>
            <Input
              id={`${fieldId}-theme`}
              variant="plain"
              value={unit.value.theme}
              onChange={setField("theme")}
              placeholder={t("overview_placeholder_theme")}
            />
          </FieldBlock>
        </div>
        <FieldBlock id={`${fieldId}-world`} label={t("overview_field_world_setting")}>
          <Textarea
            id={`${fieldId}-world`}
            variant="plain"
            value={unit.value.world_setting}
            onChange={setField("world_setting")}
            placeholder={t("overview_placeholder_world_setting")}
            className="max-h-none"
          />
        </FieldBlock>
      </div>
    );
  }

  return (
    <section
      aria-labelledby={headingId}
      data-onboarding={ONBOARDING_ANCHORS.workbenchOverview}
      className="flex flex-col gap-3"
    >
      <div className="flex min-h-7 items-center gap-2">
        <h2 id={headingId} className="flex-1 text-sm font-medium text-subtle-foreground">
          {t("overview_story_setting")}
        </h2>
        {!readOnly && !generating && canGenerate ? (
          <Button variant="ghost" size="sm" onClick={startGenerate}>
            <RefreshCw aria-hidden data-icon="inline-start" />
            {generateLabel}
          </Button>
        ) : null}
      </div>
      {description ? <p className="max-w-[40em] text-sm text-muted-foreground">{description}</p> : null}

      {generateError && !generating && !readOnly ? (
        <div role="alert" className="flex flex-col gap-1.5 text-sm text-destructive">
          {generateError.kind === "refresh" ? (
            <p>{t("overview_generate_refresh_failed")}</p>
          ) : (
            <>
              <p>{t("overview_generate_failed", { message: generateError.message })}</p>
              {generateError.truncation ? <OutputTruncationHint truncation={generateError.truncation} /> : null}
            </>
          )}
        </div>
      ) : null}

      {body}

      {readOnly ? null : <UnsavedChangesBar unit={unit} className="max-w-[40em]" />}

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("overview_regenerate_confirm_title")}</AlertDialogTitle>
            <AlertDialogDescription>
              {unit.dirty ? t("overview_regenerate_confirm_desc_dirty") : t("overview_regenerate_confirm_desc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmGenerate}>
              {unit.dirty ? t("overview_discard_and_regenerate") : t("overview_regenerate_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function FieldBlock({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {/* Fields extend by their own horizontal padding so idle body text/labels align left. */}
      <div className="-mx-2.5">{children}</div>
    </div>
  );
}
