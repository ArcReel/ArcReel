import { memo, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  DndContext,
  closestCenter,
  useSensor,
  useSensors,
  PointerSensor,
  KeyboardSensor,
} from "@dnd-kit/core";
import type { Announcements, DragEndEvent, ScreenReaderInstructions } from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Plus } from "lucide-react";
import { MentionPicker, type MentionCandidate } from "./MentionPicker";
import { RefChip } from "./RefChip";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { SHEET_FIELD, type AssetKind, type ReferenceResource } from "@/types/reference-video";
import { normalizeAssetName } from "@/utils/reference-mentions";

const PICKER_ID = "reference-panel-mention-picker";

// Drag id format: `${type}:${name}`.
const refId = (r: ReferenceResource): string => `${r.type}:${normalizeAssetName(r.name)}`;

type BucketEntry = Partial<Record<"character_sheet" | "scene_sheet" | "prop_sheet", string>>;
// bucket key 与 name 可能是 NFC/NFD 中的任一方（bucket 来自落盘的 project.json 原始 key，
// name 可能来自已归一的 references 或选择器候选），两侧归一后再比对，见
// `utils/reference-mentions.ts` 顶部注释的坐标系约定。
const sheetOf = (
  bucket: Record<string, unknown> | undefined,
  kind: AssetKind,
  name: string,
): string | null => {
  if (!bucket) return null;
  const target = normalizeAssetName(name);
  for (const key of Object.keys(bucket)) {
    if (normalizeAssetName(key) === target) {
      return (bucket[key] as BucketEntry | undefined)?.[SHEET_FIELD[kind]] ?? null;
    }
  }
  return null;
};

export interface ReferencePanelProps {
  references: ReferenceResource[];
  projectName: string;
  onReorder: (next: ReferenceResource[]) => void;
  onRemove: (ref: ReferenceResource) => void;
  /** Called when the user selects a candidate from the panel's internal picker. */
  onAdd: (ref: ReferenceResource) => void;
}

interface SortableChipProps {
  id: string;
  refItem: ReferenceResource;
  index: number;
  imageUrl: string | null;
  onRemove: (ref: ReferenceResource) => void;
}

const SortableChip = memo(function SortableChip({
  id,
  refItem,
  index,
  imageUrl,
  onRemove,
}: SortableChipProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <RefChip
      ref={setNodeRef}
      kind={refItem.type}
      name={refItem.name}
      imageUrl={imageUrl}
      index={index}
      removable
      onRemove={() => onRemove(refItem)}
      dragAttributes={attributes as unknown as Record<string, unknown>}
      dragListeners={listeners}
      isDragging={isDragging}
      style={{ transform: CSS.Transform.toString(transform), transition }}
    />
  );
});

export function ReferencePanel({
  references,
  projectName,
  onReorder,
  onRemove,
  onAdd,
}: ReferencePanelProps) {
  const { t } = useTranslation("dashboard");
  const [pickerOpen, setPickerOpen] = useState(false);
  // addButton 作为 floating-ui 的 reference 元素参与定位；用 state（而非 ref）是
  // 因为挂载后必须触发 re-render，以便 MentionPicker 的 setReference effect 能
  // 感知元素变更。同一元素也作为 outside-pointerdown 的例外目标（anchorElement）。
  const [addButtonEl, setAddButtonEl] = useState<HTMLButtonElement | null>(null);
  const characters = useProjectsStore((s) => s.currentProjectData?.characters);
  const scenes = useProjectsStore((s) => s.currentProjectData?.scenes);
  const props = useProjectsStore((s) => s.currentProjectData?.props);
  const assetFingerprints = useProjectsStore((s) => s.assetFingerprints);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // baseIds 可能重复：PATCH 接口只校验 references 逐条已登记，不校验数组内互相去重，
  // 一个 unit 的 references 里可能同时留有同一资产的 NFC/NFD 两条历史记录。sortableIds
  // 给重复的 base id 追加序号，保证 React key 与 dnd-kit sortable id 互不相同；
  // existingKeys（供候选过滤用）仍用未加序号的 baseIds，语义是「已存在该资产」而非「已存在该 id」。
  const baseIds = useMemo(() => references.map(refId), [references]);
  const existingKeys = useMemo(() => new Set(baseIds), [baseIds]);
  const sortableIds = useMemo(() => {
    const seen = new Map<string, number>();
    return baseIds.map((base) => {
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      return n === 0 ? base : `${base}#${n}`;
    });
  }, [baseIds]);

  const candidates: Record<AssetKind, MentionCandidate[]> = useMemo(() => {
    const buckets: Record<AssetKind, Record<string, unknown> | undefined> = {
      character: characters,
      scene: scenes,
      prop: props,
    };
    const out = {} as Record<AssetKind, MentionCandidate[]>;
    for (const kind of ["character", "scene", "prop"] as const) {
      out[kind] = Object.keys(buckets[kind] ?? {})
        .filter((name) => !existingKeys.has(`${kind}:${normalizeAssetName(name)}`))
        .map((name) => ({ name, imagePath: sheetOf(buckets[kind], kind, name) }));
    }
    return out;
  }, [existingKeys, characters, scenes, props]);

  // 一次性派生每个 chip 的 imageUrl，避免每个 chip 订阅 store。
  const chipData = useMemo(() => {
    const buckets: Record<AssetKind, Record<string, unknown> | undefined> = {
      character: characters,
      scene: scenes,
      prop: props,
    };
    return references.map((r) => {
      const imagePath = sheetOf(buckets[r.type], r.type, r.name);
      const fingerprint = imagePath ? (assetFingerprints[imagePath] ?? null) : null;
      const imageUrl = imagePath ? API.getFileUrl(projectName, imagePath, fingerprint) : null;
      return { ref: r, imageUrl };
    });
  }, [references, characters, scenes, props, assetFingerprints, projectName]);

  const handleAddClick = () => setPickerOpen((v) => !v);

  const indexOfId = (id: string): number => sortableIds.indexOf(id);

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromIndex = indexOfId(String(active.id));
    const toIndex = indexOfId(String(over.id));
    if (fromIndex < 0 || toIndex < 0) return;
    onReorder(arrayMove(references, fromIndex, toIndex));
  };

  // Keyboard drag announcements for screen readers.
  const announcements = useMemo<Announcements>(() => {
    const locate = (id: string) => {
      const index = sortableIds.indexOf(id);
      return { name: references[index]?.name ?? "", index: index + 1 };
    };
    return {
      onDragStart: ({ active }) => t("reference_panel_announce_pick_up", locate(String(active.id))),
      onDragOver: ({ active, over }) => {
        if (!over) return undefined;
        const { name } = locate(String(active.id));
        const { index } = locate(String(over.id));
        return t("reference_panel_announce_move", { name, index });
      },
      onDragEnd: ({ active, over }) => {
        if (!over) return undefined;
        const { name } = locate(String(active.id));
        const { index } = locate(String(over.id));
        return t("reference_panel_announce_drop", { name, index });
      },
      onDragCancel: ({ active }) => t("reference_panel_announce_cancel", locate(String(active.id))),
    };
  }, [t, references, sortableIds]);

  const screenReaderInstructions = useMemo<ScreenReaderInstructions>(
    () => ({ draggable: t("reference_panel_sr_instructions") }),
    [t],
  );

  return (
    <div className="relative flex-shrink-0 border-b border-[var(--color-hairline-soft)] bg-[oklch(0.20_0.011_265_/_0.35)] px-3 py-2.5">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-4)]">
          {t("reference_strip_label")}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-4)]">
          {references.length}
        </span>
        <span className="flex-1" />
        <span className="text-[10px] text-[var(--color-text-4)]">
          {t("reference_strip_order_hint")}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {references.length === 0 && (
          <span className="text-xs italic text-[var(--color-text-4)]">
            {t("reference_strip_empty")}
          </span>
        )}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
          accessibility={{ announcements, screenReaderInstructions }}
        >
          <SortableContext items={sortableIds} strategy={rectSortingStrategy}>
            {chipData.map((d, i) => (
              <SortableChip
                key={sortableIds[i]}
                id={sortableIds[i]}
                refItem={d.ref}
                index={i}
                imageUrl={d.imageUrl}
                onRemove={onRemove}
              />
            ))}
          </SortableContext>
        </DndContext>
        <button
          ref={setAddButtonEl}
          type="button"
          onClick={handleAddClick}
          aria-label={t("reference_strip_add")}
          aria-expanded={pickerOpen}
          aria-controls={PICKER_ID}
          className="focus-ring inline-flex items-center gap-1 rounded-full border border-dashed border-[var(--color-hairline-strong)] bg-[oklch(0.22_0.011_265_/_0.55)] px-2.5 py-1 text-xs text-[var(--color-text-3)] transition-colors hover:border-[var(--color-accent-soft)] hover:text-[var(--color-text)]"
        >
          <Plus className="h-3 w-3" aria-hidden="true" />
          <span>{t("reference_strip_add")}</span>
        </button>
      </div>
      {pickerOpen && (
        <MentionPicker
          open
          query=""
          candidates={candidates}
          projectName={projectName}
          listboxId={PICKER_ID}
          anchorElement={addButtonEl}
          onSelect={(ref) => {
            onAdd(ref);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
