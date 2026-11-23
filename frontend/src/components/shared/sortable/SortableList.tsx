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

const lockToVertical: Modifier = ({ transform }) => ({ ...transform, x: 0 });
const lockToHorizontal: Modifier = ({ transform }) => ({ ...transform, y: 0 });

interface SortableListProps<Id extends UniqueIdentifier> {
  ids: readonly Id[];
  onMove: (move: SortableMove<Id>) => void;
  canMove?: (move: SortableMove<Id>) => boolean;
  getName: (id: Id) => string;
  orientation?: "vertical" | "horizontal";
  disabled?: boolean;
  children: ReactNode;
}

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

export function SortableItem({ id, render, ...props }: SortableItemProps) {
  const { t } = useTranslation("common");
  const sortable = useSortable({ id, attributes: { roleDescription: t("sortable_role_description") } });
  const element = useRender({
    defaultTagName: "li",
    render,
    ref: sortable.setNodeRef,
    props: mergeProps<"li">(
      {
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

export function useSortableMove(id: UniqueIdentifier) {
  const { ids, allows, tryMove } = useListContext("useSortableMove");
  const index = ids.indexOf(id);
  return {
    index,
    canMoveBy: (offset: number) => allows(id, index + offset),
    moveBy: (offset: number) => tryMove(id, index + offset),
  };
}
