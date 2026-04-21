import { memo, useCallback, useMemo, useRef, useState } from "react";
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
import { GripVertical, Plus, X, ZoomIn } from "lucide-react";
import { assetColor } from "./asset-colors";
import { MentionPicker, type MentionCandidate } from "./MentionPicker";
import { ImageLightbox } from "@/components/ui/ImageLightbox";
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
  projectName: string;
  imagePath: string | null;
  thumbFingerprint: number | null;
  onRemove: (ref: ReferenceResource) => void;
  onOpenLightbox: (url: string, name: string) => void;
}

const Pill = memo(function Pill({
  refItem,
  projectName,
  imagePath,
  thumbFingerprint,
  onRemove,
  onOpenLightbox,
}: PillProps) {
  const { t } = useTranslation("dashboard");
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: refId(refItem),
  });
  const palette = assetColor(refItem.type);
  const thumbUrl = imagePath ? API.getFileUrl(projectName, imagePath, thumbFingerprint) : null;

  // Vertical card：thumbnail 上 + name 下。去掉 [图N] 索引（顺序即 [图N]，冗余文本没收益），
  // 名称去掉 @（数据就是名，@ 仅用于 prompt 里的引用语法）。拖拽握把 + 删除按钮在 hover 时浮现，
  // 静息态干净。
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`group/pill relative flex w-[88px] flex-col items-center gap-1.5 rounded-md border p-2 ${palette.bgClass} ${palette.borderClass} ${isDragging ? "opacity-60" : ""}`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={t("reference_panel_drag_aria", { name: refItem.name })}
        className="absolute left-1 top-1 z-10 cursor-grab rounded bg-black/40 p-0.5 text-gray-200 opacity-0 transition group-hover/pill:opacity-100 focus-visible:opacity-100 focus-ring"
      >
        <GripVertical className="h-3 w-3" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={() => onRemove(refItem)}
        aria-label={t("reference_panel_remove_aria", { name: refItem.name })}
        className="absolute right-1 top-1 z-10 rounded-full bg-black/50 p-0.5 text-gray-200 opacity-0 transition hover:text-red-400 group-hover/pill:opacity-100 focus-visible:opacity-100 focus-ring"
      >
        <X className="h-3 w-3" aria-hidden="true" />
      </button>
      {thumbUrl ? (
        <button
          type="button"
          onClick={() => onOpenLightbox(thumbUrl, refItem.name)}
          aria-label={t("reference_panel_zoom_aria", { name: refItem.name })}
          className="relative h-[72px] w-[72px] shrink-0 overflow-hidden rounded bg-gray-900 ring-1 ring-gray-800 transition hover:ring-indigo-400 focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:outline-none"
        >
          <img src={thumbUrl} alt="" loading="lazy" className="h-full w-full object-cover" />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition group-hover/pill:opacity-100"
          >
            <ZoomIn className="h-4 w-4 text-white" />
          </span>
        </button>
      ) : (
        <div
          aria-hidden="true"
          className={`flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded bg-gray-900 text-[11px] ${palette.textClass} ring-1 ring-gray-800`}
        >
          {refItem.name.slice(0, 1)}
        </div>
      )}
      <span
        className={`w-full truncate text-center text-[11px] ${palette.textClass}`}
        title={refItem.name}
      >
        {refItem.name}
      </span>
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
  const [lightbox, setLightbox] = useState<{ url: string; name: string } | null>(null);
  // addButton 既要走 anchorRef（给 MentionPicker 的 outside-pointerdown 例外判断用
  // `current` contains），又要作为 floating-ui 的 reference 元素参与定位——后者必须
  // 在元素挂载后触发 re-render，因此用 callback ref 同时写入 ref 和 state。
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const [addButtonEl, setAddButtonEl] = useState<HTMLButtonElement | null>(null);
  const setAddButton = useCallback((el: HTMLButtonElement | null) => {
    addButtonRef.current = el;
    setAddButtonEl(el);
  }, []);
  // Fine-grained subscriptions: depend on the specific slices we actually read,
  // so unrelated changes to currentProjectData don't force candidates to rebuild.
  const characters = useProjectsStore((s) => s.currentProjectData?.characters);
  const scenes = useProjectsStore((s) => s.currentProjectData?.scenes);
  const props = useProjectsStore((s) => s.currentProjectData?.props);
  // 直接订阅整个 fingerprints map —— store 的 getAssetFingerprint 是 stable 闭包，
  // 放进 useMemo deps 无法驱动缩略图 cache-bust（regenerate 角色图后 pillData 不会重算）。
  const assetFingerprints = useProjectsStore((s) => s.assetFingerprints);
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
      return {
        ref: r,
        imagePath,
        fingerprint: imagePath ? (assetFingerprints[imagePath] ?? null) : null,
      };
    });
  }, [references, characters, scenes, props, assetFingerprints]);

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

  const openLightbox = (url: string, name: string) => setLightbox({ url, name });

  return (
    <div className="relative border-b border-gray-800 bg-gray-950/40 p-2">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-gray-500">
          {t("reference_panel_title")}
        </span>
        <button
          ref={setAddButton}
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
            <div className="flex flex-wrap gap-2">
              {pillData.map((d) => (
                <Pill
                  key={refId(d.ref)}
                  refItem={d.ref}
                  projectName={projectName}
                  imagePath={d.imagePath}
                  thumbFingerprint={d.fingerprint}
                  onRemove={onRemove}
                  onOpenLightbox={openLightbox}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      {pickerOpen && (
        <MentionPicker
          open
          query=""
          candidates={candidates}
          projectName={projectName}
          listboxId={PICKER_ID}
          anchorRef={addButtonRef}
          anchorElement={addButtonEl}
          onSelect={(ref) => {
            onAdd(ref);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
      {lightbox && (
        <ImageLightbox src={lightbox.url} alt={lightbox.name} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}
