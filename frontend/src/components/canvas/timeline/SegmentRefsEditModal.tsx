import { useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, Check, ExternalLink, MapPin, Puzzle, SearchIcon, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character, Prop, Scene } from "@/types";
import { type AssetKind, SHEET_FIELD } from "@/types/reference-video";

type SegmentAssetKind = Exclude<AssetKind, "product">;
import { colorForName } from "@/utils/color";
import {
  characterReferenceForms,
  formatReferenceName,
  referenceInitial,
} from "@/utils/reference-mentions";

type Asset = Character | Scene | Prop;

interface RefRow {
  kind: SegmentAssetKind;
  name: string;
  thumbPath?: string;
  description?: string;
  isStale: boolean;
  /** 本集新增资产（内容确认时可选），确认时才登记。 */
  isNew?: boolean;
  /** 已选的「不登记」新增项：确认时从引用中移出，不是失效引用。 */
  isSkipped?: boolean;
}

/** 内容确认页额外的候选：本集新增项按类型给出名字。 */
export type SegmentRefsNameSets = Partial<Record<SegmentAssetKind, ReadonlySet<string>>>;

export interface SegmentRefsChanges {
  characters?: string[];
  scenes?: string[];
  props?: string[];
}

interface SegmentRefsEditModalProps {
  open: boolean;
  onClose: () => void;
  /** 确定：只带改动过的类型。返回的 Promise 落定前两个按钮禁用、不响应关闭。 */
  onSave: (changes: SegmentRefsChanges) => void | Promise<void>;
  /** 提交中：禁用按钮并忽略关闭请求；由调用方维护 */
  saving?: boolean;
  initialCharacters: string[];
  initialScenes: string[];
  initialProps: string[];
  characters: Record<string, Character>;
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
  projectName: string;
  onManageClick?: (kind: SegmentAssetKind) => void;
  /** 本集新增、确认时登记的名字；须同时出现在对应的资产字典里。 */
  newNames?: SegmentRefsNameSets;
  /** 本集新增里选了「不登记」的名字：不作候选，已选时按「确认时移出」呈现。 */
  skippedNames?: SegmentRefsNameSets;
}

function arraysEqualUnordered(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}

function getSheetPath(kind: SegmentAssetKind, asset: Asset): string | undefined {
  const value = (asset as unknown as Record<string, unknown>)[SHEET_FIELD[kind]];
  return typeof value === "string" ? value : undefined;
}

const NO_NAMES: ReadonlySet<string> = new Set();

/** 已选但不在候选里的名字：「不登记」的新增项按确认时移出呈现，其余是失效引用。 */
function appendUnlisted(
  rows: RefRow[],
  kind: SegmentAssetKind,
  selected: string[],
  known: (name: string) => boolean,
  skipped: ReadonlySet<string>,
): RefRow[] {
  for (const name of selected.filter((n) => !known(n)).sort()) {
    rows.push(skipped.has(name) ? { kind, name, isStale: false, isSkipped: true } : { kind, name, isStale: true });
  }
  return rows;
}

function buildRows<A extends Asset>(
  kind: SegmentAssetKind,
  dict: Record<string, A>,
  selected: string[],
  newNames: ReadonlySet<string> = NO_NAMES,
  skipped: ReadonlySet<string> = NO_NAMES,
): RefRow[] {
  const rows: RefRow[] = Object.entries(dict)
    .map(([name, asset]) => ({
      kind,
      name,
      thumbPath: getSheetPath(kind, asset),
      description: asset.description,
      isStale: false,
      isNew: newNames.has(name),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
  return appendUnlisted(rows, kind, selected, (n) => n in dict, skipped);
}

/**
 * 角色行按「形态」而非「资产条目」列：`characters_in_*` 收的是引用名，衍生
 * （`本体名/衍生名`）与本体一样可选（见 `docs/adr/0072`），各带自己的资产图与变化描述。
 */
function buildCharacterRows(
  characters: Record<string, Character>,
  selected: string[],
  newNames: ReadonlySet<string> = NO_NAMES,
  skipped: ReadonlySet<string> = NO_NAMES,
): RefRow[] {
  const forms = characterReferenceForms(characters);
  const rows: RefRow[] = forms
    .map((form) => ({
      kind: "character" as const,
      name: form.name,
      thumbPath: getSheetPath("character", form.asset),
      description: form.asset.description,
      isStale: false,
      isNew: newNames.has(form.name),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const known = new Set(forms.map((form) => form.name));
  return appendUnlisted(rows, "character", selected, (n) => known.has(n), skipped);
}

export function SegmentRefsEditModal({
  open,
  onClose,
  onSave,
  saving = false,
  initialCharacters,
  initialScenes,
  initialProps,
  characters,
  scenes,
  props,
  projectName,
  onManageClick,
  newNames,
  skippedNames,
}: SegmentRefsEditModalProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");
  const [tempChars, setTempChars] = useState<string[]>(initialCharacters);
  const [tempScenes, setTempScenes] = useState<string[]>(initialScenes);
  const [tempProps, setTempProps] = useState<string[]>(initialProps);

  const tempCharsSet = new Set(tempChars);
  const tempScenesSet = new Set(tempScenes);
  const tempPropsSet = new Set(tempProps);

  const charRows = useMemo(
    () => buildCharacterRows(characters, tempChars, newNames?.character, skippedNames?.character),
    [characters, tempChars, newNames, skippedNames],
  );
  const sceneRows = useMemo(
    () => buildRows("scene", scenes, tempScenes, newNames?.scene, skippedNames?.scene),
    [scenes, tempScenes, newNames, skippedNames],
  );
  const propRows = useMemo(
    () => buildRows("prop", props, tempProps, newNames?.prop, skippedNames?.prop),
    [props, tempProps, newNames, skippedNames],
  );

  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    const filterRows = (rows: RefRow[]) =>
      q
        ? rows.filter(
            (r) =>
              r.name.toLowerCase().includes(q) || formatReferenceName(r.name).toLowerCase().includes(q),
          )
        : rows;
    return {
      character: filterRows(charRows),
      scene: filterRows(sceneRows),
      prop: filterRows(propRows),
    };
  }, [charRows, sceneRows, propRows, q]);

  // stale 计数基于未过滤的完整 rows，避免搜索词把 stale 项过滤后徽标消失
  const countSelectedStale = (rows: RefRow[], set: Set<string>) =>
    rows.reduce((n, r) => (r.isStale && set.has(r.name) ? n + 1 : n), 0);
  const staleCounts = {
    character: countSelectedStale(charRows, tempCharsSet),
    scene: countSelectedStale(sceneRows, tempScenesSet),
    prop: countSelectedStale(propRows, tempPropsSet),
  };

  const setterByKind: Record<SegmentAssetKind, typeof setTempChars> = {
    character: setTempChars,
    scene: setTempScenes,
    prop: setTempProps,
  };
  const toggle = (kind: SegmentAssetKind, name: string) => {
    setterByKind[kind]((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  };

  const charChanged = !arraysEqualUnordered(tempChars, initialCharacters);
  const scenesChanged = !arraysEqualUnordered(tempScenes, initialScenes);
  const propsChanged = !arraysEqualUnordered(tempProps, initialProps);
  const hasChanges = charChanged || scenesChanged || propsChanged;

  const handleSave = async () => {
    const changes: SegmentRefsChanges = {};
    if (charChanged) changes.characters = tempChars;
    if (scenesChanged) changes.scenes = tempScenes;
    if (propsChanged) changes.props = tempProps;
    await onSave(changes);
  };

  const sectionProps = {
    selectedSets: { character: tempCharsSet, scene: tempScenesSet, prop: tempPropsSet },
    onToggle: toggle,
    projectName,
    onManageClick,
    hasQuery: !!q,
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{t("segment_refs_edit_title")}</DialogTitle>
          <InputGroup className="mt-1">
            <InputGroupInput
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("segment_refs_search_placeholder")}
              aria-label={t("segment_refs_search_placeholder")}
              autoComplete="off"
              spellCheck={false}
            />
            <InputGroupAddon>
              <SearchIcon aria-hidden />
            </InputGroupAddon>
          </InputGroup>
        </DialogHeader>

        <DialogBody>
          <div className="flex flex-col gap-5">
            <Section
              {...sectionProps}
              title={t("segment_refs_badge_character")}
              kind="character"
              icon={<User aria-hidden className="size-3.5" />}
              rows={filtered.character}
              staleCount={staleCounts.character}
              emptyText={t("segment_refs_empty_characters")}
            />
            <Section
              {...sectionProps}
              title={t("segment_refs_badge_scene")}
              kind="scene"
              icon={<MapPin aria-hidden className="size-3.5" />}
              rows={filtered.scene}
              staleCount={staleCounts.scene}
              emptyText={t("segment_refs_empty_clues")}
            />
            <Section
              {...sectionProps}
              title={t("segment_refs_badge_prop")}
              kind="prop"
              icon={<Puzzle aria-hidden className="size-3.5" />}
              rows={filtered.prop}
              staleCount={staleCounts.prop}
              emptyText={t("segment_refs_empty_clues")}
            />
          </div>
        </DialogBody>

        <DialogFooter>
          <span role="status" className={cn("flex-1 text-xs", hasChanges ? "text-foreground" : "text-muted-foreground")}>
            {hasChanges ? t("segment_refs_changes_pending") : t("segment_refs_no_changes")}
          </span>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {t("segment_refs_cancel")}
          </Button>
          <Button disabled={!hasChanges || saving} onClick={() => void handleSave()}>
            {t("segment_refs_apply")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface SectionProps {
  title: string;
  kind: SegmentAssetKind;
  icon: ReactNode;
  rows: RefRow[];
  selectedSets: Record<SegmentAssetKind, Set<string>>;
  /** 已选且失效的引用数；由 parent 基于未过滤集合计算，避免搜索过滤后徽标消失 */
  staleCount: number;
  onToggle: (kind: SegmentAssetKind, name: string) => void;
  projectName: string;
  emptyText: string;
  onManageClick?: (kind: SegmentAssetKind) => void;
  hasQuery: boolean;
}

function Section({
  title,
  kind,
  icon,
  rows,
  selectedSets,
  staleCount,
  onToggle,
  projectName,
  emptyText,
  onManageClick,
  hasQuery,
}: SectionProps) {
  const { t } = useTranslation("dashboard");
  const selectedSet = selectedSets[kind];
  const selectedCount = rows.reduce((n, r) => (selectedSet.has(r.name) ? n + 1 : n), 0);
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="flex text-muted-foreground">{icon}</span>
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        {rows.length > 0 && (
          <span className="num text-xs text-muted-foreground">
            {selectedCount}/{rows.length}
          </span>
        )}
        {staleCount > 0 && (
          <span className="num inline-flex items-center gap-1 rounded-full bg-warn/10 px-1.5 py-0.5 text-xs text-warn ring-1 ring-warn/30">
            <AlertTriangle aria-hidden className="size-3" />
            {t("segment_refs_stale_badge", { count: staleCount })}
          </span>
        )}
      </div>
      {rows.length === 0 && hasQuery && (
        <p className="px-2 py-1 text-xs text-muted-foreground">{t("segment_refs_search_empty")}</p>
      )}
      {rows.length === 0 && !hasQuery && (
        <div className="flex items-center gap-2 rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
          <span className="flex-1">{emptyText}</span>
          {onManageClick && (
            <Button variant="link" size="xs" onClick={() => onManageClick(kind)}>
              {t("segment_refs_manage_link")}
              <ExternalLink aria-hidden data-icon="inline-end" />
            </Button>
          )}
        </div>
      )}
      {rows.length > 0 && (
        <div className="grid grid-cols-2 gap-1.5">
          {rows.map((r) => (
            <Row
              key={`${kind}-${r.name}`}
              row={r}
              selected={selectedSet.has(r.name)}
              onToggle={() => onToggle(r.kind, r.name)}
              projectName={projectName}
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
}

/** 一条候选：整行是一个切换按钮，按下即选中。失效引用与「不登记」的新增项在第二行说明。 */
function Row({ row, selected, onToggle, projectName }: RowProps) {
  const { t } = useTranslation("dashboard");
  const sheetFp = useProjectsStore((s) =>
    row.thumbPath ? s.getAssetFingerprint(row.thumbPath) : null,
  );
  const thumbShape = row.kind === "character" ? "rounded-full" : "rounded-md";
  const showImage = !!row.thumbPath && !row.isStale;
  const displayName = formatReferenceName(row.name);
  const secondLine = row.isSkipped
    ? t("segment_refs_skipped_hint")
    : row.isStale
      ? t("segment_refs_stale_hint")
      : row.description?.split("\n")[0];

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      className={cn(
        "focus-ring flex min-w-0 items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition-colors",
        row.isSkipped
          ? "border-dashed border-border"
          : row.isStale
            ? "border-warn/30 bg-warn/10"
            : selected
              ? "border-primary/40 bg-primary/12 hover:border-primary"
              : "border-border bg-muted/30 hover:border-input hover:bg-muted/60",
      )}
    >
      {showImage ? (
        <img
          src={API.getFileUrl(projectName, row.thumbPath!, sheetFp)}
          alt=""
          className={cn("size-8 shrink-0 object-cover", thumbShape)}
        />
      ) : (
        <span
          aria-hidden
          className={cn(
            "grid size-8 shrink-0 place-items-center text-xs font-semibold",
            thumbShape,
            row.isStale ? "bg-warn/10 text-warn" : cn(colorForName(row.name), "text-foreground"),
          )}
        >
          {referenceInitial(row.name)}
        </span>
      )}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className={cn("flex min-w-0 items-center gap-1.5 text-sm", selected ? "font-semibold" : "font-medium", row.isStale ? "text-warn" : "text-foreground")}>
          <TruncatedText text={displayName} focusable={false} className="min-w-0" />
          {row.isNew && (
            <span className="shrink-0 rounded-sm border border-border px-1 text-xs font-normal text-subtle-foreground">
              {t("segment_refs_new_tag")}
            </span>
          )}
        </span>
        {secondLine ? (
          <span className={cn("truncate text-xs", row.isStale ? "text-warn" : "text-subtle-foreground")}>{secondLine}</span>
        ) : null}
      </span>
      <span
        aria-hidden
        className={cn(
          "grid size-5 shrink-0 place-items-center rounded-full border transition-colors",
          selected ? "border-primary bg-primary text-primary-foreground" : "border-border text-transparent",
        )}
      >
        <Check className="size-3" strokeWidth={3} />
      </span>
    </button>
  );
}
