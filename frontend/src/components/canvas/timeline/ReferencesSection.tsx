import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Edit3, MapPin, Plus, Puzzle, User } from "lucide-react";
import { InfoTip, InfoTipContent, InfoTipTrigger } from "@/components/ui/info-tip";
import { TooltipIconButton } from "./TooltipIconButton";
import { AvatarStack } from "@/components/canvas/timeline/AvatarStack";
import { ClueStack } from "@/components/canvas/timeline/ClueStack";
import {
  SegmentRefsEditModal,
  type SegmentRefsChanges,
} from "@/components/canvas/timeline/SegmentRefsEditModal";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character, PlanNewAsset } from "@/types";
import { planReferenceCandidates } from "@/utils/plan-new-assets";
import { resolveCharacterForm } from "@/utils/reference-mentions";
import { charactersFieldFor, type EditorContentMode } from "@/utils/script-shape";

interface ReferencesSectionProps {
  projectName: string;
  contentMode: EditorContentMode;
  characterNames: string[];
  sceneNames: string[];
  propNames: string[];
  onSave: (patch: Record<string, string[]>) => void | Promise<void>;
  disabled?: boolean;
  disabledHint?: string;
  /** Content-confirmation page: new episode entries not marked for non-registration join registered assets as candidates. */
  newAssets?: readonly PlanNewAsset[];
}

const EMPTY_DICT = Object.freeze({});

function countMissing(names: string[], dict: Record<string, unknown>): number {
  let n = 0;
  for (const name of names) if (!Object.hasOwn(dict, name)) n += 1;
  return n;
}

/** Character references may use `本体/衍生`: these names are not top-level character entries, so determine registration according to their form. */
function countMissingCharacters(names: string[], characters: Record<string, Character>): number {
  let n = 0;
  for (const name of names) if (resolveCharacterForm(characters, name) === undefined) n += 1;
  return n;
}

export function ReferencesSection({
  projectName,
  contentMode,
  characterNames,
  sceneNames,
  propNames,
  onSave,
  disabled,
  disabledHint,
  newAssets,
}: ReferencesSectionProps) {
  const { t } = useTranslation("dashboard");
  const project = useProjectsStore((s) => s.currentProjectData);
  // Materialize the `?? {}` fallback as a stable reference with useMemo, avoiding hook-dependency recalculation every render
  const candidates = useMemo(
    () =>
      planReferenceCandidates(
        {
          characters: project?.characters ?? EMPTY_DICT,
          scenes: project?.scenes ?? EMPTY_DICT,
          props: project?.props ?? EMPTY_DICT,
        },
        newAssets ?? [],
      ),
    [project, newAssets],
  );
  const { characters, scenes, props, newNames, skippedNames } = candidates;
  const [open, setOpen] = useState(false);

  const charField = charactersFieldFor(contentMode);

  const totalCount = characterNames.length + sceneNames.length + propNames.length;
  const isEmpty = totalCount === 0;

  const totalStale = useMemo(() => {
    // Before project loading finishes, the empty dictionary falsely marks all reference names stale; skip calculation then
    if (!project) return 0;
    // Remove new entries marked for non-registration from references at confirmation; they do not count as stale references.
    const listed = (names: string[], skipped: ReadonlySet<string>) => names.filter((name) => !skipped.has(name));
    return (
      countMissingCharacters(listed(characterNames, skippedNames.character), characters) +
      countMissing(listed(sceneNames, skippedNames.scene), scenes) +
      countMissing(listed(propNames, skippedNames.prop), props)
    );
  }, [project, characterNames, sceneNames, propNames, characters, scenes, props, skippedNames]);

  const [saving, setSaving] = useState(false);

  const handleSave = async (changes: SegmentRefsChanges) => {
    const patch: Record<string, string[]> = {};
    if (changes.characters !== undefined) patch[charField] = changes.characters;
    if (changes.scenes !== undefined) patch.scenes = changes.scenes;
    if (changes.props !== undefined) patch.props = changes.props;
    if (Object.keys(patch).length === 0) {
      setOpen(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(patch);
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const openModal = () => {
    if (disabled) return;
    setOpen(true);
  };

  const heading = <h3 className="text-xs font-medium text-muted-foreground">{t("eyebrow_segment_refs")}</h3>;

  const modal = open ? (
    <SegmentRefsEditModal
      open={open}
      onClose={() => setOpen(false)}
      onSave={handleSave}
      saving={saving}
      initialCharacters={characterNames}
      initialScenes={sceneNames}
      initialProps={propNames}
      characters={characters}
      scenes={scenes}
      props={props}
      projectName={projectName}
      newNames={newNames}
      skippedNames={skippedNames}
    />
  ) : null;

  if (isEmpty) {
    return (
      <section className="flex flex-col gap-2">
        <div className="flex min-h-7 items-center">{heading}</div>
        <button
          type="button"
          onClick={openModal}
          disabled={disabled}
          title={disabled ? disabledHint : undefined}
          className="focus-ring flex w-full items-center gap-2.5 rounded-lg border border-dashed border-border px-3 py-2.5 text-left text-muted-foreground transition-colors hover:border-solid hover:border-input disabled:pointer-events-none disabled:opacity-50"
        >
          <span className="flex-1 truncate text-xs">{t("references_empty_full")}</span>
          <span className="inline-flex shrink-0 items-center gap-1 text-xs text-primary">
            <Plus className="size-3" aria-hidden="true" />
            <span>{t("references_add_cta")}</span>
          </span>
        </button>
        {modal}
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <div className="flex min-h-7 items-center gap-2">
        {heading}
        {totalStale > 0 && (
          <InfoTip>
            <InfoTipTrigger
              render={
                <button
                  type="button"
                  className="num inline-flex items-center gap-1 rounded-full bg-warn/10 px-1.5 py-0.5 text-xs text-warn ring-1 ring-warn/30"
                />
              }
            >
              <AlertTriangle aria-hidden className="size-3" />
              {t("segment_refs_stale_badge", { count: totalStale })}
            </InfoTipTrigger>
            <InfoTipContent title={t("segment_refs_stale_badge", { count: totalStale })}>{t("segment_refs_stale_hint")}</InfoTipContent>
          </InfoTip>
        )}
        <span className="flex-1" />
        <TooltipIconButton
          label={t("segment_refs_edit_button")}
          hint={disabledHint}
          disabled={disabled}
          onClick={openModal}
          size="icon-xs"
        >
          <Edit3 aria-hidden />
        </TooltipIconButton>
      </div>

      <button
        type="button"
        onClick={openModal}
        disabled={disabled}
        title={disabled ? disabledHint : undefined}
        className="focus-ring flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border/50 bg-muted/30 px-3 py-2 text-left transition-colors hover:border-input disabled:cursor-not-allowed"
      >
        {characterNames.length > 0 && (
          <Group
            icon={<User className="size-3" aria-hidden="true" />}
            label={t("references_badge_character")}
            count={characterNames.length}
          >
            <AvatarStack
              names={characterNames}
              characters={characters}
              projectName={projectName}
              maxShow={4}
            />
          </Group>
        )}
        {sceneNames.length > 0 && (
          <Group
            icon={<MapPin className="size-3" aria-hidden="true" />}
            label={t("references_badge_scene")}
            count={sceneNames.length}
          >
            <ClueStack
              sceneNames={sceneNames}
              propNames={[]}
              scenes={scenes}
              props={props}
              projectName={projectName}
              maxShow={4}
            />
          </Group>
        )}
        {propNames.length > 0 && (
          <Group
            icon={<Puzzle className="size-3" aria-hidden="true" />}
            label={t("references_badge_prop")}
            count={propNames.length}
          >
            <ClueStack
              sceneNames={[]}
              propNames={propNames}
              scenes={scenes}
              props={props}
              projectName={projectName}
              maxShow={4}
            />
          </Group>
        )}
      </button>

      {modal}
    </section>
  );
}

function Group({
  icon,
  label,
  count,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      {children}
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        {icon}
        <span>{label}</span>
        <span className="num text-subtle-foreground">{count}</span>
      </span>
    </div>
  );
}
