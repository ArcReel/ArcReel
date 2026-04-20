import { memo, useMemo, useRef, useState } from "react";
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
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Plus, X } from "lucide-react";
import { assetColor } from "./asset-colors";
import { MentionPicker, type MentionCandidate } from "./MentionPicker";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { SHEET_FIELD, type AssetKind, type ReferenceResource } from "@/types/reference-video";

const PICKER_ID = "reference-panel-mention-picker";

// Drag id format: `${type}:${name}`. Split on the first ":" so CJK names survive.
const refId = (r: ReferenceResource): string => `${r.type}:${r.name}`;
const refNameFromId = (id: string): string => id.slice(id.indexOf(":") + 1);

type BucketEntry = Partial<Record<"character_sheet" | "scene_sheet" | "prop_sheet", string>>;
const sheetOf = (bucket: Record<string, unknown> | undefined, kind: AssetKind, name: string): string | null =>
  (bucket?.[name] as BucketEntry | undefined)?.[SHEET_FIELD[kind]] ?? null;

export interface ReferencePanelProps {
  references: ReferenceResource[];
  projectName: string;
  onReorder: (next: ReferenceResource[]) => void;
  onRemove: (ref: ReferenceResource) => void;
  /** Called when the user selects a candidate from the panel's internal picker. */
  onAdd: (ref: ReferenceResource) => void;
}

interface PillProps {
  refItem: ReferenceResource;
  index: number;
  projectName: string;
  imagePath: string | null;
  thumbFingerprint: number | null;
  onRemove: (ref: ReferenceResource) => void;
}

const Pill = memo(function Pill({
  refItem,
  index,
  projectName,
  imagePath,
  thumbFingerprint,
  onRemove,
}: PillProps) {
  const { t } = useTranslation("dashboard");
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: refId(refItem),
  });
  const palette = assetColor(refItem.type);
  const thumbUrl = imagePath ? API.getFileUrl(projectName, imagePath, thumbFingerprint) : null;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${palette.textClass} ${palette.bgClass} ${palette.borderClass} ${isDragging ? "opacity-50" : ""}`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={t("reference_panel_drag_aria", { name: refItem.name })}
        className="cursor-grab font-mono text-[10px] text-gray-500 hover:text-gray-300"
      >
        {t("reference_panel_pill_index", { n: index + 1 })}
      </button>
      {thumbUrl && (
        <img src={thumbUrl} alt="" className="h-5 w-5 rounded object-cover" />
      )}
      <span className="truncate max-w-[120px]" title={refItem.name}>@{refItem.name}</span>
      <button
        type="button"
        onClick={() => onRemove(refItem)}
        aria-label={t("reference_panel_remove_aria", { name: refItem.name })}
        className="text-gray-500 hover:text-red-400"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
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
  const addButtonRef = useRef<HTMLButtonElement>(null);
  // Fine-grained subscriptions: depend on the specific slices we actually read,
  // so unrelated changes to currentProjectData don't force candidates to rebuild.
  const characters = useProjectsStore((s) => s.currentProjectData?.characters);
  const scenes = useProjectsStore((s) => s.currentProjectData?.scenes);
  const props = useProjectsStore((s) => s.currentProjectData?.props);
  const getFp = useProjectsStore((s) => s.getAssetFingerprint);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const existingKeys = useMemo(() => new Set(references.map(refId)), [references]);

  const candidates: Record<AssetKind, MentionCandidate[]> = useMemo(() => {
    const buckets: Record<AssetKind, Record<string, unknown> | undefined> = {
      character: characters,
      scene: scenes,
      prop: props,
    };
    const out = {} as Record<AssetKind, MentionCandidate[]>;
    for (const kind of ["character", "scene", "prop"] as const) {
      out[kind] = Object.keys(buckets[kind] ?? {})
        .filter((name) => !existingKeys.has(`${kind}:${name}`))
        .map((name) => ({ name, imagePath: sheetOf(buckets[kind], kind, name) }));
    }
    return out;
  }, [existingKeys, characters, scenes, props]);

  // 一次性派生每个 pill 的 imagePath + fingerprint，避免 Pill 订阅 store。
  const pillData = useMemo(() => {
    const buckets: Record<AssetKind, Record<string, unknown> | undefined> = {
      character: characters,
      scene: scenes,
      prop: props,
    };
    return references.map((r) => {
      const imagePath = sheetOf(buckets[r.type], r.type, r.name);
      return { ref: r, imagePath, fingerprint: imagePath ? getFp(imagePath) : null };
    });
  }, [references, characters, scenes, props, getFp]);

  const handleAddClick = () => setPickerOpen((v) => !v);

  const indexOfId = (id: string): number => references.findIndex((r) => refId(r) === id);

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromIndex = indexOfId(String(active.id));
    const toIndex = indexOfId(String(over.id));
    if (fromIndex < 0 || toIndex < 0) return;
    onReorder(arrayMove(references, fromIndex, toIndex));
  };

  // Keyboard drag announcements for screen readers. dnd-kit fires these on
  // Space pickup / arrow-key move / Space drop / Esc cancel.
  const announcements = useMemo<Announcements>(() => {
    const locate = (id: string) => ({
      name: refNameFromId(id),
      index: references.findIndex((r) => refId(r) === id) + 1,
    });
    return {
      onDragStart: ({ active }) => t("reference_panel_announce_pick_up", locate(String(active.id))),
      onDragOver: ({ active, over }) => {
        if (!over) return undefined;
        const { index } = locate(String(over.id));
        return t("reference_panel_announce_move", { name: refNameFromId(String(active.id)), index });
      },
      onDragEnd: ({ active, over }) => {
        if (!over) return undefined;
        const { index } = locate(String(over.id));
        return t("reference_panel_announce_drop", { name: refNameFromId(String(active.id)), index });
      },
      onDragCancel: ({ active }) =>
        t("reference_panel_announce_cancel", { name: refNameFromId(String(active.id)) }),
    };
  }, [t, references]);

  const screenReaderInstructions = useMemo<ScreenReaderInstructions>(
    () => ({ draggable: t("reference_panel_sr_instructions") }),
    [t],
  );

  return (
    <div className="relative border-t border-gray-800 bg-gray-950/40 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-gray-500">
          {t("reference_panel_title")}
        </span>
        <button
          ref={addButtonRef}
          type="button"
          onClick={handleAddClick}
          aria-label={t("reference_panel_add")}
          aria-expanded={pickerOpen}
          aria-controls={PICKER_ID}
          className="inline-flex items-center gap-1 rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300 hover:border-indigo-500 hover:text-indigo-300"
        >
          <Plus className="h-3 w-3" />
          {t("reference_panel_add")}
        </button>
      </div>
      {references.length === 0 ? (
        <p className="text-xs text-gray-500">{t("reference_panel_empty")}</p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
          accessibility={{ announcements, screenReaderInstructions }}
        >
          <SortableContext items={references.map(refId)} strategy={horizontalListSortingStrategy}>
            <div className="flex flex-wrap gap-1.5">
              {pillData.map((d, i) => (
                <Pill
                  key={refId(d.ref)}
                  refItem={d.ref}
                  index={i}
                  projectName={projectName}
                  imagePath={d.imagePath}
                  thumbFingerprint={d.fingerprint}
                  onRemove={onRemove}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      {pickerOpen && (
        <div id={PICKER_ID} className="absolute right-2 top-8 z-30">
          <MentionPicker
            open
            query=""
            candidates={candidates}
            anchorRef={addButtonRef}
            onSelect={(ref) => {
              onAdd(ref);
              setPickerOpen(false);
            }}
            onClose={() => setPickerOpen(false)}
          />
        </div>
      )}
    </div>
  );
}
