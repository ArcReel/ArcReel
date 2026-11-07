import { createContext, useContext, useMemo, useRef, type ComponentProps, type ReactNode } from "react";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type Modifier,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";
import { GripVertical } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { BufferedKeyboardSensor } from "./BufferedKeyboardSensor";

/** One move: `id` from zero-based `from` to `to`, with `ids` holding complete postmove order. */
export interface SortableMove<Id extends UniqueIdentifier> {
  id: Id;
  from: number;
  to: number;
  ids: Id[];
}

interface SortableListContextValue {
  ids: readonly UniqueIdentifier[];
  disabled: boolean;
  tryMove: (id: UniqueIdentifier, to: number) => boolean;
  allows: (id: UniqueIdentifier, to: number) => boolean;
}

const SortableListContext = createContext<SortableListContextValue | null>(null);

function useListContext(component: string): SortableListContextValue {
  const context = useContext(SortableListContext);
  if (!context) throw new Error(`${component} 必须放在 SortableList 内`);
  return context;
}

function movedIds<Id extends UniqueIdentifier>(ids: readonly Id[], from: number, to: number): Id[] {
  const next = [...ids];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// Move along list axes only, preventing horizontal/vertical drift outside lists during dragging.
const lockToVertical: Modifier = ({ transform }) => ({ ...transform, x: 0 });
const lockToHorizontal: Modifier = ({ transform }) => ({ ...transform, y: 0 });

interface SortableListProps<Id extends UniqueIdentifier> {
  /** Current order; render entries using `SortableItem` with `id` from here. */
  ids: readonly Id[];
  /** Called after drops/`useSortableMove`; callers update local order/submit, owning rollback on failure. */
  onMove: (move: SortableMove<Id>) => void;
  /** Reject targets returning false; drops restore original positions and corresponding `useSortableMove` directions become unavailable. */
  canMove?: (move: SortableMove<Id>) => boolean;
  /** Entry names for screen-reader announcements. */
  getName: (id: Id) => string;
  orientation?: "vertical" | "horizontal";
  /** Temporarily disallow sorting (e.g. search-filtered lists): disable handles/move actions. */
  disabled?: boolean;
  children: ReactNode;
}

/**
 * Sortable lists: mouse/touch handle dragging, or focus handles then Space to pick up, arrows to move, Space to drop, Esc to cancel;
 * announce pickup/movement/drop/cancellation. Supply behavior only without containers; callers write list elements,
 * using `SortableItem` entries/`SortableHandle` handles. Non-drag movement (Move up/down menus)
 * uses `useSortableMove`.
 */
export function SortableList<Id extends UniqueIdentifier>({
  ids,
  onMove,
  canMove,
  getName,
  orientation = "vertical",
  disabled = false,
  children,
}: SortableListProps<Id>) {
  const { t } = useTranslation("common");
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(BufferedKeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const context = useMemo<SortableListContextValue>(() => {
    const plan = (id: UniqueIdentifier, to: number): SortableMove<Id> | null => {
      const from = ids.indexOf(id as Id);
      if (disabled || from < 0 || to < 0 || to >= ids.length || from === to) return null;
      const move = { id: id as Id, from, to, ids: movedIds(ids, from, to) };
      return canMove && !canMove(move) ? null : move;
    };
    return {
      ids,
      disabled,
      allows: (id, to) => plan(id, to) !== null,
      tryMove: (id, to) => {
        const move = plan(id, to);
        if (move) onMove(move);
        return move !== null;
      },
    };
  }, [ids, disabled, canMove, onMove]);

  // Pickup makes dnd-kit report onDragOver again with the item itself as target; unchanged targets stay silent to preserve Picked up announcements.
  const lastOverId = useRef<UniqueIdentifier | null>(null);
  const name = (id: UniqueIdentifier) => getName(id as Id);
  const position = (id: UniqueIdentifier) => ids.indexOf(id as Id) + 1;
  const total = ids.length;
  const announcements: Announcements = {
    onDragStart: ({ active }) => {
      lastOverId.current = active.id;
      return t("sortable_picked_up", { name: name(active.id), position: position(active.id), total });
    },
    onDragOver: ({ active, over }) => {
      if (!over || over.id === lastOverId.current) return undefined;
      lastOverId.current = over.id;
      return t("sortable_moved", { name: name(active.id), position: position(over.id), total });
    },
    onDragEnd: ({ active, over }) => {
      if (!over) return t("sortable_cancelled", { name: name(active.id), position: position(active.id) });
      const to = ids.indexOf(over.id as Id);
      if (over.id !== active.id && !context.allows(active.id, to)) {
        return t("sortable_rejected", { name: name(active.id), position: position(active.id) });
      }
      return t("sortable_dropped", { name: name(active.id), position: to + 1, total });
    },
    onDragCancel: ({ active }) => t("sortable_cancelled", { name: name(active.id), position: position(active.id) }),
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || over.id === active.id) return;
    context.tryMove(active.id, ids.indexOf(over.id as Id));
  };

  return (
    <SortableListContext.Provider value={context}>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        modifiers={[orientation === "vertical" ? lockToVertical : lockToHorizontal]}
        accessibility={{ announcements, screenReaderInstructions: { draggable: t("sortable_instructions") } }}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={[...ids]}
          strategy={orientation === "vertical" ? verticalListSortingStrategy : horizontalListSortingStrategy}
          disabled={disabled}
        >
          {children}
        </SortableContext>
      </DndContext>
    </SortableListContext.Provider>
  );
}

type SortableState = ReturnType<typeof useSortable>;

const SortableItemContext = createContext<SortableState | null>(null);

type SortableItemProps = Omit<useRender.ComponentProps<"li">, "id"> & { id: UniqueIdentifier };

/**
 * List entries default to `li`; `render` changes elements. Dragging adds `data-dragging`,
 * styled by callers through `data-dragging:` variants.
 */
export function SortableItem({ id, render, ...props }: SortableItemProps) {
  const { t } = useTranslation("common");
  const sortable = useSortable({ id, attributes: { roleDescription: t("sortable_role_description") } });
  const element = useRender({
    defaultTagName: "li",
    render,
    ref: sortable.setNodeRef,
    props: mergeProps<"li">(
      {
        // Only dnd-kit writes per-frame inline drag transforms; lift dragged items above siblings
        // so later-positioned items cannot cover them when moving later in lists.
        style: {
          transform: CSS.Translate.toString(sortable.transform),
          transition: sortable.transition,
          position: "relative",
          zIndex: sortable.isDragging ? 1 : undefined,
        },
        "data-dragging": sortable.isDragging ? "" : undefined,
      } as ComponentProps<"li">,
      props,
    ),
  });
  return <SortableItemContext.Provider value={sortable}>{element}</SortableItemContext.Provider>;
}

/** Drag handles: icon buttons with `label` identifying reordered items, e.g. Reorder Official market. */
export function SortableHandle({ label }: { label: string }) {
  const sortable = useContext(SortableItemContext);
  if (!sortable) throw new Error("SortableHandle 必须放在 SortableItem 内");
  const { disabled } = useListContext("SortableHandle");
  const { setActivatorNodeRef, attributes, listeners, isDragging } = sortable;
  return (
    <Button
      ref={setActivatorNodeRef}
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      disabled={disabled}
      className="cursor-grab touch-none data-dragging:cursor-grabbing"
      data-dragging={isDragging ? "" : undefined}
      {...attributes}
      {...listeners}

    >
      <GripVertical aria-hidden />
    </Button>
  );
}

/**
 * Non-drag movement: `moveBy(-1)` moves one earlier, `moveBy(1)` one later, for Move up/down menu entries
 * (WCAG 2.5.7 requires single-pointer alternatives to dragging). Disable corresponding entries when `canMoveBy` is false.
 */
export function useSortableMove(id: UniqueIdentifier) {
  const { ids, allows, tryMove } = useListContext("useSortableMove");
  const index = ids.indexOf(id);
  return {
    index,
    canMoveBy: (offset: number) => allows(id, index + offset),
    moveBy: (offset: number) => tryMove(id, index + offset),
  };
}
