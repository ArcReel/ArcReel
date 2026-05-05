import { useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  Check,
  ExternalLink,
  Link2,
  MapPin,
  Puzzle,
  Search,
  User,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character, Prop, Scene } from "@/types";
import { type AssetKind, SHEET_FIELD } from "@/types/reference-video";
import { colorForName } from "@/utils/color";
import { WARM_TONE } from "@/utils/severity-tone";

type Asset = Character | Scene | Prop;

interface RefRow {
  kind: AssetKind;
  name: string;
  thumbPath?: string;
  description?: string;
  isStale: boolean;
}

export interface SegmentRefsChanges {
  characters?: string[];
  scenes?: string[];
  props?: string[];
}

interface SegmentRefsEditModalProps {
  open: boolean;
  onClose: () => void;
  onSave: (changes: SegmentRefsChanges) => void;
  initialCharacters: string[];
  initialScenes: string[];
  initialProps: string[];
  characters: Record<string, Character>;
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
  projectName: string;
  onManageClick?: (kind: AssetKind) => void;
}

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

function arraysEqualUnordered(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}

function getSheetPath(kind: AssetKind, asset: Asset): string | undefined {
  const value = (asset as unknown as Record<string, unknown>)[SHEET_FIELD[kind]];
  return typeof value === "string" ? value : undefined;
}

function buildRows<A extends Asset>(
  kind: AssetKind,
  dict: Record<string, A>,
  selected: string[],
): RefRow[] {
  const rows: RefRow[] = Object.entries(dict)
    .map(([name, asset]) => ({
      kind,
      name,
      thumbPath: getSheetPath(kind, asset),
      description: asset.description,
      isStale: false,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const stale = selected.filter((n) => !(n in dict)).sort();
  for (const name of stale) rows.push({ kind, name, isStale: true });
  return rows;
}

export function SegmentRefsEditModal({
  open,
  onClose,
  onSave,
  initialCharacters,
  initialScenes,
  initialProps,
  characters,
  scenes,
  props,
  projectName,
  onManageClick,
}: SegmentRefsEditModalProps) {
  const { t } = useTranslation("dashboard");
  const dialogRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [tempChars, setTempChars] = useState<string[]>(initialCharacters);
  const [tempScenes, setTempScenes] = useState<string[]>(initialScenes);
  const [tempProps, setTempProps] = useState<string[]>(initialProps);

  useFocusTrap(dialogRef, open);
  useEscapeClose(onClose, open);

  const tempCharsSet = new Set(tempChars);
  const tempScenesSet = new Set(tempScenes);
  const tempPropsSet = new Set(tempProps);

  const charRows = useMemo(
    () => buildRows("character", characters, tempChars),
    [characters, tempChars],
  );
  const sceneRows = useMemo(
    () => buildRows("scene", scenes, tempScenes),
    [scenes, tempScenes],
  );
  const propRows = useMemo(
    () => buildRows("prop", props, tempProps),
    [props, tempProps],
  );

  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    const filterRows = (rows: RefRow[]) =>
      q ? rows.filter((r) => r.name.toLowerCase().includes(q)) : rows;
    return {
      character: filterRows(charRows),
      scene: filterRows(sceneRows),
      prop: filterRows(propRows),
    };
  }, [charRows, sceneRows, propRows, q]);

  const setterByKind: Record<AssetKind, typeof setTempChars> = {
    character: setTempChars,
    scene: setTempScenes,
    prop: setTempProps,
  };
  const toggle = (kind: AssetKind, name: string) => {
    setterByKind[kind]((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  };

  const charChanged = !arraysEqualUnordered(tempChars, initialCharacters);
  const scenesChanged = !arraysEqualUnordered(tempScenes, initialScenes);
  const propsChanged = !arraysEqualUnordered(tempProps, initialProps);
  const hasChanges = charChanged || scenesChanged || propsChanged;

  const handleSave = () => {
    const changes: SegmentRefsChanges = {};
    if (charChanged) changes.characters = tempChars;
    if (scenesChanged) changes.scenes = tempScenes;
    if (propsChanged) changes.props = tempProps;
    onSave(changes);
  };

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        aria-hidden="true"
        onClick={onClose}
        className="absolute inset-0"
        style={{
          background: "oklch(0 0 0 / 0.65)",
          backdropFilter: "blur(2px)",
          WebkitBackdropFilter: "blur(2px)",
        }}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t("segment_refs_edit_title")}
        className="relative flex max-h-[80vh] w-[680px] max-w-[96vw] flex-col overflow-hidden rounded-2xl"
        style={{
          background: PANEL_BG,
          border: "1px solid var(--color-hairline)",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          boxShadow:
            "0 24px 60px -12px oklch(0 0 0 / 0.6), inset 0 1px 0 oklch(1 0 0 / 0.05)",
        }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
          }}
        />

        {/* Header */}
        <div
          className="flex items-center gap-3 px-5 py-4"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
              boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
            }}
          >
            <Link2 className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3
              className="display-serif truncate text-[15px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t("segment_refs_edit_title")}
            </h3>
            <div
              className="num text-[10px] uppercase"
              style={{
                color: "var(--color-text-4)",
                letterSpacing: "1.0px",
              }}
            >
              References · Scope
            </div>
          </div>

          <div
            className="flex w-44 items-center gap-2 rounded-md px-2.5 py-1.5 sm:w-52"
            style={{
              background: "oklch(0.16 0.010 265 / 0.6)",
              border: "1px solid var(--color-hairline)",
            }}
          >
            <Search
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "var(--color-text-4)" }}
              aria-hidden="true"
            />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("segment_refs_search_placeholder")}
              aria-label={t("segment_refs_search_placeholder")}
              autoComplete="off"
              spellCheck={false}
              className="focus-ring min-w-0 flex-1 bg-transparent text-[13px] outline-none"
              style={{ color: "var(--color-text)" }}
            />
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label={t("segment_refs_close")}
            className="focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--color-text)";
              e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-3)";
              e.currentTarget.style.background = "transparent";
            }}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4">
          <Section
            title={t("segment_refs_badge_character")}
            kind="character"
            icon={<User className="h-3.5 w-3.5" aria-hidden="true" />}
            rows={filtered.character}
            selectedSet={tempCharsSet}
            onToggle={toggle}
            projectName={projectName}
            emptyText={t("segment_refs_empty_characters")}
            manageText={t("segment_refs_manage_link")}
            onManageClick={onManageClick}
            hasQuery={!!q}
            staleHint={t("segment_refs_stale_hint")}
            searchEmptyText={t("segment_refs_search_empty")}
          />
          <Section
            title={t("segment_refs_badge_scene")}
            kind="scene"
            icon={<MapPin className="h-3.5 w-3.5" aria-hidden="true" />}
            rows={filtered.scene}
            selectedSet={tempScenesSet}
            onToggle={toggle}
            projectName={projectName}
            emptyText={t("segment_refs_empty_clues")}
            manageText={t("segment_refs_manage_link")}
            onManageClick={onManageClick}
            hasQuery={!!q}
            staleHint={t("segment_refs_stale_hint")}
            searchEmptyText={t("segment_refs_search_empty")}
          />
          <Section
            title={t("segment_refs_badge_prop")}
            kind="prop"
            icon={<Puzzle className="h-3.5 w-3.5" aria-hidden="true" />}
            rows={filtered.prop}
            selectedSet={tempPropsSet}
            onToggle={toggle}
            projectName={projectName}
            emptyText={t("segment_refs_empty_clues")}
            manageText={t("segment_refs_manage_link")}
            onManageClick={onManageClick}
            hasQuery={!!q}
            staleHint={t("segment_refs_stale_hint")}
            searchEmptyText={t("segment_refs_search_empty")}
          />
        </div>

        {/* Footer */}
        <div
          className="flex items-center gap-2 px-5 py-3"
          style={{
            borderTop: "1px solid var(--color-hairline-soft)",
            background: "oklch(0.17 0.010 250 / 0.5)",
          }}
        >
          <span
            className="num flex-1 text-[11px] uppercase"
            style={{
              letterSpacing: "0.8px",
              color: hasChanges ? WARM_TONE.color : "var(--color-text-4)",
            }}
          >
            {hasChanges
              ? t("segment_refs_changes_pending")
              : t("segment_refs_no_changes")}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring rounded-md px-3 py-1.5 text-[12px] transition-colors"
            style={{
              color: "var(--color-text-3)",
              border: "1px solid var(--color-hairline)",
              background: "oklch(0.22 0.011 265 / 0.5)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "var(--color-text)";
              e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-3)";
              e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
            }}
          >
            {t("segment_refs_cancel")}
          </button>
          <button
            type="button"
            disabled={!hasChanges}
            onClick={handleSave}
            className="focus-ring rounded-md px-3.5 py-1.5 text-[12px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-40"
            style={{
              color: "oklch(0.14 0 0)",
              background:
                "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
              boxShadow:
                "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -6px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
            }}
            onMouseEnter={(e) => {
              if (!e.currentTarget.disabled)
                e.currentTarget.style.transform = "translateY(-1px)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = "translateY(0)";
            }}
          >
            {t("segment_refs_save")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

interface SectionProps {
  title: string;
  kind: AssetKind;
  icon: ReactNode;
  rows: RefRow[];
  selectedSet: Set<string>;
  onToggle: (kind: AssetKind, name: string) => void;
  projectName: string;
  emptyText: string;
  manageText: string;
  onManageClick?: (kind: AssetKind) => void;
  hasQuery: boolean;
  staleHint: string;
  searchEmptyText: string;
}

function Section({
  title,
  kind,
  icon,
  rows,
  selectedSet,
  onToggle,
  projectName,
  emptyText,
  manageText,
  onManageClick,
  hasQuery,
  staleHint,
  searchEmptyText,
}: SectionProps) {
  const selectedCount = rows.reduce(
    (n, r) => (selectedSet.has(r.name) ? n + 1 : n),
    0,
  );
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <span style={{ color: "var(--color-text-3)" }}>{icon}</span>
        <h4
          className="num text-[10.5px] font-bold uppercase"
          style={{
            color: "var(--color-text-3)",
            letterSpacing: "1.0px",
          }}
        >
          {title}
        </h4>
        {rows.length > 0 && (
          <span
            className="num text-[10.5px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {selectedCount}/{rows.length}
          </span>
        )}
      </div>
      {rows.length === 0 && hasQuery && (
        <p
          className="px-2 py-1 text-[11.5px]"
          style={{ color: "var(--color-text-4)" }}
        >
          {searchEmptyText}
        </p>
      )}
      {rows.length === 0 && !hasQuery && (
        <div
          className="flex items-center gap-2 rounded-md px-3 py-2 text-[12px]"
          style={{
            border: "1px dashed var(--color-hairline)",
            color: "var(--color-text-4)",
          }}
        >
          <span className="flex-1">{emptyText}</span>
          {onManageClick && (
            <button
              type="button"
              onClick={() => onManageClick(kind)}
              className="focus-ring inline-flex items-center gap-1 rounded transition-colors"
              style={{ color: "var(--color-accent-2)" }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--color-text)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-accent-2)";
              }}
            >
              <span>{manageText}</span>
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </button>
          )}
        </div>
      )}
      {rows.length > 0 && (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {rows.map((r) => (
            <Row
              key={`${kind}-${r.name}`}
              row={r}
              selected={selectedSet.has(r.name)}
              onToggle={() => onToggle(r.kind, r.name)}
              projectName={projectName}
              staleHint={staleHint}
            />
          ))}
        </div>
      )}
    </section>
  );
}

interface RowProps {
  row: RefRow;
  selected: boolean;
  onToggle: () => void;
  projectName: string;
  staleHint: string;
}

function Row({ row, selected, onToggle, projectName, staleHint }: RowProps) {
  const sheetFp = useProjectsStore((s) =>
    row.thumbPath ? s.getAssetFingerprint(row.thumbPath) : null,
  );
  const isCharacter = row.kind === "character";
  const thumbShape = isCharacter ? "rounded-full" : "rounded-md";
  const showImage = !!row.thumbPath && !row.isStale;

  const baseStyle = row.isStale
    ? {
        background: WARM_TONE.soft,
        border: `1px solid ${WARM_TONE.ring}`,
      }
    : selected
      ? {
          background:
            "linear-gradient(135deg, var(--color-accent-dim) 0%, oklch(0.20 0.011 265 / 0.5) 60%)",
          border: "1px solid var(--color-accent-soft)",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 4px 14px -6px var(--color-accent-glow)",
        }
      : {
          background: "oklch(0.20 0.011 265 / 0.4)",
          border: "1px solid var(--color-hairline)",
        };

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      title={row.isStale ? staleHint : row.name}
      className="focus-ring group flex items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors"
      style={baseStyle}
      onMouseEnter={(e) => {
        if (row.isStale) return;
        if (selected) {
          e.currentTarget.style.borderColor = "var(--color-accent)";
        } else {
          e.currentTarget.style.borderColor = "var(--color-hairline-strong)";
          e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.7)";
        }
      }}
      onMouseLeave={(e) => {
        if (row.isStale) {
          e.currentTarget.style.borderColor = WARM_TONE.ring;
          return;
        }
        if (selected) {
          e.currentTarget.style.borderColor = "var(--color-accent-soft)";
        } else {
          e.currentTarget.style.borderColor = "var(--color-hairline)";
          e.currentTarget.style.background = "oklch(0.20 0.011 265 / 0.4)";
        }
      }}
    >
      {showImage ? (
        <img
          src={API.getFileUrl(projectName, row.thumbPath!, sheetFp)}
          alt={row.name}
          className={`h-8 w-8 shrink-0 object-cover ${thumbShape}`}
        />
      ) : (
        <span
          className={`grid h-8 w-8 shrink-0 place-items-center text-[10px] font-semibold text-white ${thumbShape} ${
            row.isStale ? "" : colorForName(row.name)
          }`}
          style={
            row.isStale
              ? { background: WARM_TONE.soft, color: WARM_TONE.color }
              : undefined
          }
        >
          {row.name.charAt(0)}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p
          className={`truncate text-[13px] ${
            selected ? "font-semibold" : "font-medium"
          }`}
          style={{
            color: row.isStale ? WARM_TONE.color : "var(--color-text)",
          }}
        >
          {row.name}
        </p>
        {row.isStale ? (
          <p
            className="truncate text-[11px]"
            style={{ color: WARM_TONE.color }}
          >
            {staleHint}
          </p>
        ) : (
          row.description && (
            <p
              className="truncate text-[11px]"
              style={{ color: "var(--color-text-4)" }}
            >
              {row.description.split("\n")[0]}
            </p>
          )
        )}
      </div>
      <span
        aria-hidden="true"
        className="grid h-5 w-5 shrink-0 place-items-center rounded-full transition-colors"
        style={
          selected
            ? {
                color: "oklch(0.14 0 0)",
                background:
                  "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                border: "1px solid var(--color-accent-soft)",
                boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.35)",
              }
            : {
                color: "var(--color-text-4)",
                background: "transparent",
                border: "1px solid var(--color-hairline)",
              }
        }
      >
        <Check className="h-3 w-3" strokeWidth={3} />
      </span>
    </button>
  );
}
