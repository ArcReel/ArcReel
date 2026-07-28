import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Edit3, MapPin, Plus, Puzzle, User } from "lucide-react";
import { AvatarStack } from "@/components/ui/AvatarStack";
import { ClueStack } from "@/components/ui/ClueStack";
import {
  SegmentRefsEditModal,
  type SegmentRefsChanges,
} from "@/components/ui/SegmentRefsEditModal";
import { useProjectsStore } from "@/stores/projects-store";
import { charactersFieldFor, type EditorContentMode } from "@/utils/script-shape";
import { WARM_TONE } from "@/utils/severity-tone";

interface ReferencesSectionProps {
  projectName: string;
  contentMode: EditorContentMode;
  characterNames: string[];
  sceneNames: string[];
  propNames: string[];
  onSave: (patch: Record<string, string[]>) => void | Promise<void>;
  disabled?: boolean;
  disabledHint?: string;
}

const EMPTY_DICT = Object.freeze({});

function countMissing(names: string[], dict: Record<string, unknown>): number {
  let n = 0;
  for (const name of names) if (!Object.hasOwn(dict, name)) n += 1;
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
}: ReferencesSectionProps) {
  const { t } = useTranslation("dashboard");
  const project = useProjectsStore((s) => s.currentProjectData);
  // 用 useMemo 把 `?? {}` fallback 物化成稳定引用，避免 hook deps 每次重算
  const characters = useMemo(() => project?.characters ?? EMPTY_DICT, [project]);
  const scenes = useMemo(() => project?.scenes ?? EMPTY_DICT, [project]);
  const props = useMemo(() => project?.props ?? EMPTY_DICT, [project]);
  const [open, setOpen] = useState(false);

  const charField = charactersFieldFor(contentMode);

  const totalCount = characterNames.length + sceneNames.length + propNames.length;
  const isEmpty = totalCount === 0;

  const totalStale = useMemo(() => {
    // project 未加载完时字典为空，会把所有已引用名都误判为 stale；此时跳过计算
    if (!project) return 0;
    return (
      countMissing(characterNames, characters) +
      countMissing(sceneNames, scenes) +
      countMissing(propNames, props)
    );
  }, [project, characterNames, sceneNames, propNames, characters, scenes, props]);

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

  const eyebrow = (
    <div
      className="text-[10.5px] font-bold uppercase"
      style={{
        color: "var(--color-text-4)",
        letterSpacing: "1px",
        fontFamily: "var(--font-mono)",
      }}
    >
      {t("eyebrow_segment_refs")}
    </div>
  );

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
    />
  ) : null;

  if (isEmpty) {
    return (
      <div>
        <div className="mb-2 flex items-center justify-between">{eyebrow}</div>
        <button
          type="button"
          onClick={openModal}
          disabled={disabled}
          title={disabled ? disabledHint : t("references_add_cta")}
          className="focus-ring group flex w-full items-center gap-2.5 rounded-md px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            border: "1px dashed var(--color-hairline)",
            color: "var(--color-text-4)",
            background: "transparent",
          }}
          onMouseEnter={(e) => {
            if (disabled) return;
            e.currentTarget.style.borderColor = "var(--color-hairline-strong)";
            e.currentTarget.style.borderStyle = "solid";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = "var(--color-hairline)";
            e.currentTarget.style.borderStyle = "dashed";
          }}
        >
          <span className="flex-1 truncate text-[12px]">
            {t("references_empty_full")}
          </span>
          <span
            className="num inline-flex shrink-0 items-center gap-1 text-[11px]"
            style={{ color: "var(--color-accent-2)" }}
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            <span>{t("references_add_cta")}</span>
          </span>
        </button>
        {modal}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        {eyebrow}
        {totalStale > 0 && (
          <span
            className="num inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]"
            style={{
              background: WARM_TONE.soft,
              border: `1px solid ${WARM_TONE.ring}`,
              color: WARM_TONE.color,
            }}
            title={t("segment_refs_stale_hint")}
          >
            <span aria-hidden="true">⚠</span>
            <span>{t("segment_refs_stale_badge", { count: totalStale })}</span>
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={openModal}
          disabled={disabled}
          title={disabled ? disabledHint : t("segment_refs_edit_button")}
          aria-label={t("segment_refs_edit_button")}
          className="focus-ring inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            color: "var(--color-text-3)",
            background: "transparent",
          }}
          onMouseEnter={(e) => {
            if (disabled) return;
            e.currentTarget.style.color = "var(--color-accent-2)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-3)";
          }}
        >
          <Edit3 className="h-3 w-3" aria-hidden="true" />
        </button>
      </div>

      <button
        type="button"
        onClick={openModal}
        disabled={disabled}
        title={disabled ? disabledHint : t("segment_refs_edit_button")}
        className="focus-ring group flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60"
        style={{
          border: "1px solid var(--color-hairline-soft)",
          background: "oklch(0.20 0.011 265 / 0.4)",
        }}
        onMouseEnter={(e) => {
          if (disabled) return;
          e.currentTarget.style.borderColor = "var(--color-hairline-strong)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = "var(--color-hairline-soft)";
        }}
      >
        {characterNames.length > 0 && (
          <Group
            icon={<User className="h-3 w-3" aria-hidden="true" />}
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
            icon={<MapPin className="h-3 w-3" aria-hidden="true" />}
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
            icon={<Puzzle className="h-3 w-3" aria-hidden="true" />}
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
    </div>
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
      <span
        className="inline-flex items-center gap-1 text-[11px]"
        style={{ color: "var(--color-text-3)" }}
      >
        <span style={{ color: "var(--color-text-4)" }}>{icon}</span>
        <span>{label}</span>
        <span className="num" style={{ color: "var(--color-text-2)" }}>
          {count}
        </span>
      </span>
    </div>
  );
}
