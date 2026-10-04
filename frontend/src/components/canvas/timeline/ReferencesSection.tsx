import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Edit3, MapPin, Plus, Puzzle, User } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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
  /** 内容确认页：本集新增项，非「不登记」的项与已登记资产一起作候选。 */
  newAssets?: readonly PlanNewAsset[];
}

const EMPTY_DICT = Object.freeze({});

function countMissing(names: string[], dict: Record<string, unknown>): number {
  let n = 0;
  for (const name of names) if (!Object.hasOwn(dict, name)) n += 1;
  return n;
}

/** 角色引用可以是 `本体/衍生`：这种名字不在角色表顶层，未登记须按形态判定。 */
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
  // 用 useMemo 把 `?? {}` fallback 物化成稳定引用，避免 hook deps 每次重算
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
    // project 未加载完时字典为空，会把所有已引用名都误判为 stale；此时跳过计算
    if (!project) return 0;
    // 「不登记」的新增项确认时从引用中移出，不算失效引用。
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
          <Tooltip>
            <TooltipTrigger
              render={
                // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 失效说明只在提示里，须能用键盘聚焦打开；它没有可执行的动作，不渲染为 button
                <span tabIndex={0} className="num focus-ring inline-flex items-center gap-1 rounded-full bg-warn/10 px-1.5 py-0.5 text-xs text-warn ring-1 ring-warn/30" />
              }
            >
              <AlertTriangle aria-hidden className="size-3" />
              {t("segment_refs_stale_badge", { count: totalStale })}
              <span className="sr-only">{t("segment_refs_stale_hint")}</span>
            </TooltipTrigger>
            <TooltipContent>{t("segment_refs_stale_hint")}</TooltipContent>
          </Tooltip>
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
