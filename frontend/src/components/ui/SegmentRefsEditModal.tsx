import { useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ExternalLink, MapPin, Puzzle, Search, User, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character, Prop, Scene } from "@/types";
import { type AssetKind, SHEET_FIELD } from "@/types/reference-video";
import { colorForName } from "@/utils/color";

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
        className="absolute inset-0 bg-black/70"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t("segment_refs_edit_title")}
        className="relative flex max-h-[80vh] w-[640px] max-w-[96vw] flex-col rounded-lg border border-gray-700 bg-gray-900 shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-gray-800 px-4 py-3">
          <h3 className="flex-1 text-sm font-semibold text-white">
            {t("segment_refs_edit_title")}
          </h3>
          <div className="flex w-32 items-center gap-2 rounded border border-gray-700 bg-gray-800 px-3 py-1.5 sm:w-48">
            <Search className="h-3.5 w-3.5 text-gray-500" aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("segment_refs_search_placeholder")}
              aria-label={t("segment_refs_search_placeholder")}
              autoComplete="off"
              spellCheck={false}
              className="flex-1 bg-transparent text-sm text-gray-200 outline-none"
            />
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("segment_refs_close")}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-3">
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

        <div className="flex items-center gap-2 border-t border-gray-800 px-4 py-3">
          <span
            className={`flex-1 text-xs ${
              hasChanges ? "text-amber-400" : "text-gray-400"
            }`}
          >
            {hasChanges
              ? t("segment_refs_changes_pending")
              : t("segment_refs_no_changes")}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded bg-gray-800 px-3 py-1 text-xs text-gray-300 transition-colors hover:bg-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40"
          >
            {t("segment_refs_cancel")}
          </button>
          <button
            type="button"
            disabled={!hasChanges}
            onClick={handleSave}
            className="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40"
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
      <div className="mb-2 flex items-center gap-1.5">
        <span className="text-gray-500">{icon}</span>
        <h4 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
          {title}
        </h4>
        {rows.length > 0 && (
          <span className="text-[11px] tabular-nums text-gray-500">
            {selectedCount}/{rows.length}
          </span>
        )}
      </div>
      {rows.length === 0 && hasQuery && (
        <p className="px-2 py-1 text-xs text-gray-600">{searchEmptyText}</p>
      )}
      {rows.length === 0 && !hasQuery && (
        <div className="flex items-center gap-2 rounded border border-dashed border-gray-800 px-3 py-2 text-xs text-gray-500">
          <span className="flex-1">{emptyText}</span>
          {onManageClick && (
            <button
              type="button"
              onClick={() => onManageClick(kind)}
              className="flex items-center gap-1 text-indigo-400 transition-colors hover:text-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40"
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
  const thumbShape = isCharacter ? "rounded-full" : "rounded";
  const showImage = !!row.thumbPath && !row.isStale;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      title={row.isStale ? staleHint : row.name}
      className={`group flex items-center gap-2 rounded border px-2 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40 ${
        row.isStale
          ? "border-amber-800/50 bg-amber-900/10 hover:border-amber-700"
          : selected
            ? "border-indigo-500/60 bg-indigo-950/40 hover:border-indigo-400"
            : "border-gray-800 bg-gray-800/40 hover:border-gray-700 hover:bg-gray-800"
      }`}
    >
      {showImage ? (
        <img
          src={API.getFileUrl(projectName, row.thumbPath!, sheetFp)}
          alt={row.name}
          className={`h-8 w-8 shrink-0 ${thumbShape} object-cover`}
        />
      ) : (
        <span
          className={`flex h-8 w-8 shrink-0 items-center justify-center text-[10px] font-semibold text-white ${thumbShape} ${
            row.isStale ? "bg-amber-700/40" : colorForName(row.name)
          }`}
        >
          {row.name.charAt(0)}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p
          className={`truncate text-sm ${
            selected ? "font-semibold" : "font-medium"
          } ${row.isStale ? "text-amber-300" : "text-white"}`}
        >
          {row.name}
        </p>
        {row.isStale ? (
          <p className="truncate text-[11px] text-amber-400">{staleHint}</p>
        ) : (
          row.description && (
            <p className="truncate text-[11px] text-gray-500">
              {row.description.split("\n")[0]}
            </p>
          )
        )}
      </div>
      <span
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors ${
          selected
            ? "border-indigo-500 bg-indigo-500 text-white"
            : "border-gray-700 bg-transparent text-gray-700 group-hover:border-gray-600 group-hover:text-gray-600"
        }`}
        aria-hidden="true"
      >
        <Check className="h-3 w-3" strokeWidth={3} />
      </span>
    </button>
  );
}
