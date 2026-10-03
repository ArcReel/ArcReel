import { createContext, useContext, useMemo, useRef, type ComponentProps, type ReactNode } from "react";
import {
  DndContext,
  KeyboardSensor,
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

/** 一次移动：把 `id` 从第 `from` 项移到第 `to` 项（从 0 计），`ids` 是移动后的完整顺序。 */
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

// 只沿列表方向移动，拖动时条目不会横向（或纵向）漂出列表。
const lockToVertical: Modifier = ({ transform }) => ({ ...transform, x: 0 });
const lockToHorizontal: Modifier = ({ transform }) => ({ ...transform, y: 0 });

interface SortableListProps<Id extends UniqueIdentifier> {
  /** 当前顺序。条目在列表里用 `SortableItem` 渲染，`id` 取自这里。 */
  ids: readonly Id[];
  /** 松手或经 `useSortableMove` 移动后调用；调用方据此改本地顺序并提交，失败时自行回滚。 */
  onMove: (move: SortableMove<Id>) => void;
  /** 返回 false 的落点不接受：拖放后条目回到原处，`useSortableMove` 对应的方向不可用。 */
  canMove?: (move: SortableMove<Id>) => boolean;
  /** 读屏播报里的条目名称。 */
  getName: (id: Id) => string;
  orientation?: "vertical" | "horizontal";
  /** 暂时不允许排序（如列表正在按搜索词筛选）：把手禁用，移动动作不可用。 */
  disabled?: boolean;
  children: ReactNode;
}

/**
 * 可排序列表：鼠标、触控拖动把手，或聚焦把手后按空格键拿起、方向键移动、再按空格键放下，Esc 取消；
 * 拿起、移动、放下与取消都经读屏播报。只提供排序行为，不渲染容器：调用方自己写列表元素，
 * 每一项用 `SortableItem`，把手用 `SortableHandle`；不依赖拖动的移动方式（「上移」「下移」菜单）
 * 用 `useSortableMove`。
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
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
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

  // 拿起时 dnd-kit 会以条目自身为落点再报一次 onDragOver；落点没变就不播报，免得盖掉「已拿起」。
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
 * 列表中的一项，默认渲染为 `li`，用 `render` 换成别的元素。拖动中带 `data-dragging`，
 * 调用方用 `data-dragging:` 变体表达拖动态。
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
        // 拖动位移只能由 dnd-kit 逐帧写入内联样式；拖动中的条目抬到同列表的其他条目之上，
        // 否则向后移动时会被后面的条目盖住。
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

/** 拖动把手：图标按钮，`label` 写明调整的是哪一项的顺序，如「调整『官方市场』的顺序」。 */
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
 * 不靠拖动的移动：`moveBy(-1)` 前移一位、`moveBy(1)` 后移一位，供「上移」「下移」这类菜单项使用
 * （WCAG 2.5.7 要求拖动之外另有单指操作的方式）。`canMoveBy` 为 false 时对应的菜单项应禁用。
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
