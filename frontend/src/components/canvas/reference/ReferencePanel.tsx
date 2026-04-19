import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  DndContext,
  closestCenter,
  useSensor,
  useSensors,
  PointerSensor,
  KeyboardSensor,
} from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
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
import type { AssetKind, ReferenceResource } from "@/types/reference-video";

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
  onRemove: () => void;
}

function Pill({ refItem, index, projectName, onRemove }: PillProps) {
  const { t } = useTranslation("dashboard");
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `${refItem.type}:${refItem.name}`,
  });
  const palette = assetColor(refItem.type);
  const project = useProjectsStore((s) => s.currentProjectData);

  let imagePath: string | null = null;
  if (refItem.type === "character") {
    imagePath = (project?.characters?.[refItem.name] as { character_sheet?: string } | undefined)?.character_sheet ?? null;
  } else if (refItem.type === "scene") {
    imagePath = (project?.scenes?.[refItem.name] as { scene_sheet?: string } | undefined)?.scene_sheet ?? null;
  } else if (refItem.type === "prop") {
    imagePath = (project?.props?.[refItem.name] as { prop_sheet?: string } | undefined)?.prop_sheet ?? null;
  }
  const thumbUrl = imagePath ? API.getFileUrl(projectName, imagePath) : null;

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
        [图{index + 1}]
      </button>
      {thumbUrl && (
        <img src={thumbUrl} alt="" className="h-5 w-5 rounded object-cover" />
      )}
      <span className="truncate max-w-[120px]">@{refItem.name}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("reference_panel_remove_aria", { name: refItem.name })}
        className="text-gray-500 hover:text-red-400"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

export function ReferencePanel({
  references,
  projectName,
  onReorder,
  onRemove,
  onAdd,
}: ReferencePanelProps) {
  const { t } = useTranslation("dashboard");
  const [pickerOpen, setPickerOpen] = useState(false);
  const project = useProjectsStore((s) => s.currentProjectData);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const existingKeys = new Set(references.map((r) => `${r.type}:${r.name}`));

  const candidates: Record<AssetKind, MentionCandidate[]> = {
    character: Object.entries(project?.characters ?? {})
      .filter(([name]) => !existingKeys.has(`character:${name}`))
      .map(([name, data]) => ({
        name,
        imagePath: (data as { character_sheet?: string }).character_sheet ?? null,
      })),
    scene: Object.entries(project?.scenes ?? {})
      .filter(([name]) => !existingKeys.has(`scene:${name}`))
      .map(([name, data]) => ({
        name,
        imagePath: (data as { scene_sheet?: string }).scene_sheet ?? null,
      })),
    prop: Object.entries(project?.props ?? {})
      .filter(([name]) => !existingKeys.has(`prop:${name}`))
      .map(([name, data]) => ({
        name,
        imagePath: (data as { prop_sheet?: string }).prop_sheet ?? null,
      })),
  };

  const handleAddClick = () => setPickerOpen((v) => !v);

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromIndex = references.findIndex((r) => `${r.type}:${r.name}` === active.id);
    const toIndex = references.findIndex((r) => `${r.type}:${r.name}` === over.id);
    if (fromIndex < 0 || toIndex < 0) return;
    onReorder(arrayMove(references, fromIndex, toIndex));
  };

  return (
    <div className="relative border-t border-gray-800 bg-gray-950/40 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-gray-500">
          {t("reference_panel_title")}
        </span>
        <button
          type="button"
          onClick={handleAddClick}
          aria-label={t("reference_panel_add")}
          className="inline-flex items-center gap-1 rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300 hover:border-indigo-500 hover:text-indigo-300"
        >
          <Plus className="h-3 w-3" />
          {t("reference_panel_add")}
        </button>
      </div>
      {references.length === 0 ? (
        <p className="text-xs text-gray-500">{t("reference_panel_empty")}</p>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext
            items={references.map((r) => `${r.type}:${r.name}`)}
            strategy={horizontalListSortingStrategy}
          >
            <div className="flex flex-wrap gap-1.5">
              {references.map((r, i) => (
                <Pill
                  key={`${r.type}:${r.name}`}
                  refItem={r}
                  index={i}
                  projectName={projectName}
                  onRemove={() => onRemove(r)}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      {pickerOpen && (
        <div className="absolute right-2 top-8 z-30">
          <MentionPicker
            open
            query=""
            candidates={candidates}
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
