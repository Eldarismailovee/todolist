import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { useDroppable } from '@dnd-kit/core';
import { useMemo, useState } from 'react';

import { useDeleteTask, useMoveTask } from '../api/queries';
import type { BoardColumn, Task } from '../api/types';

interface Props {
  columns: BoardColumn[];
  tasks: Task[];
  userId: number;
  onOpenTask: (task: Task) => void;
}

const NO_COLUMN = 'none';

function columnKey(columnId: number | null): string {
  return columnId === null ? NO_COLUMN : String(columnId);
}

function dueLabel(task: Task): { text: string; overdue: boolean } | null {
  if (!task.due_at) return null;
  const due = new Date(task.due_at);
  return {
    text: due.toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }),
    overdue: due.getTime() < Date.now() && task.completed_at === null,
  };
}

const TaskCard = ({
  task,
  onOpen,
  onDelete,
  dragging,
}: {
  task: Task;
  onOpen: () => void;
  onDelete: () => void;
  dragging?: boolean;
}) => {
  const due = dueLabel(task);
  return (
    <article
      className={[
        'group rounded-xl border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-800 dark:bg-gray-900',
        dragging ? 'opacity-40' : '',
        task.completed_at ? 'opacity-70' : '',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          onClick={onOpen}
          className={[
            'min-w-0 flex-1 text-left text-sm font-medium',
            task.completed_at ? 'line-through decoration-gray-400' : '',
          ].join(' ')}
        >
          {task.title}
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label={`Удалить «${task.title}»`}
          className="shrink-0 text-xs text-gray-400 opacity-0 transition-opacity group-hover:opacity-100 hover:text-red-500 focus:opacity-100"
        >
          ×
        </button>
      </div>

      {task.description && (
        <p className="mt-1 line-clamp-2 text-xs text-gray-500 dark:text-gray-400">
          {task.description}
        </p>
      )}

      {(due || (task.tags?.length ?? 0) > 0) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {due && (
            <span
              className={[
                'rounded-lg px-1.5 py-0.5 text-[11px]',
                due.overdue
                  ? 'bg-red-500/15 text-red-700 dark:text-red-300'
                  : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
              ].join(' ')}
            >
              {due.overdue ? '⚠ ' : '⏱ '}
              {due.text}
            </span>
          )}
          {(task.tags ?? []).map((tag) => (
            <span
              key={tag.id}
              className="rounded-lg px-1.5 py-0.5 text-[11px]"
              style={{ backgroundColor: `${tag.color}22`, color: tag.color }}
            >
              #{tag.name}
            </span>
          ))}
        </div>
      )}
    </article>
  );
};

const SortableTask = ({
  task,
  onOpen,
  onDelete,
}: {
  task: Task;
  onOpen: () => void;
  onDelete: () => void;
}) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: task.id,
    data: { columnId: task.column_id },
  });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      {...attributes}
      {...listeners}
      className="touch-none"
    >
      <TaskCard task={task} onOpen={onOpen} onDelete={onDelete} dragging={isDragging} />
    </div>
  );
};

const Column = ({
  column,
  tasks,
  onOpenTask,
  onDeleteTask,
}: {
  column: { id: number | null; title: string };
  tasks: Task[];
  onOpenTask: (task: Task) => void;
  onDeleteTask: (task: Task) => void;
}) => {
  // Отдельная droppable-зона: в пустую колонку иначе нечего «уронить».
  const { setNodeRef, isOver } = useDroppable({
    id: `column-${columnKey(column.id)}`,
    data: { columnId: column.id },
  });

  return (
    <section
      ref={setNodeRef}
      aria-label={column.title}
      className={[
        'flex min-h-[12rem] w-72 shrink-0 flex-col gap-2 rounded-2xl border p-3 transition-colors',
        isOver
          ? 'border-indigo-400 bg-indigo-500/5'
          : 'border-gray-200 bg-gray-50/70 dark:border-gray-800 dark:bg-gray-900/40',
      ].join(' ')}
    >
      <header className="flex items-center justify-between px-1">
        <h3 className="text-sm font-semibold tracking-tight">{column.title}</h3>
        <span className="text-xs text-gray-500">{tasks.length}</span>
      </header>

      <SortableContext items={tasks.map((task) => task.id)} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col gap-2">
          {tasks.map((task) => (
            <SortableTask
              key={task.id}
              task={task}
              onOpen={() => onOpenTask(task)}
              onDelete={() => onDeleteTask(task)}
            />
          ))}
        </div>
      </SortableContext>
    </section>
  );
};

export const KanbanBoard = ({ columns, tasks, userId, onOpenTask }: Props) => {
  const move = useMoveTask(userId);
  const remove = useDeleteTask(userId);
  const [dragged, setDragged] = useState<Task | null>(null);

  const sensors = useSensors(
    // Небольшой порог: клик по карточке не должен начинать перетаскивание.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const grouped = useMemo(() => {
    const map = new Map<string, Task[]>();
    map.set(NO_COLUMN, []);
    for (const column of columns) map.set(String(column.id), []);
    for (const task of tasks) {
      const key = columnKey(task.column_id);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(task);
    }
    for (const list of map.values()) list.sort((left, right) => left.position - right.position);
    return map;
  }, [columns, tasks]);

  const orphans = grouped.get(NO_COLUMN) ?? [];

  function handleDragStart(event: DragStartEvent) {
    setDragged(tasks.find((task) => task.id === event.active.id) ?? null);
  }

  function handleDragEnd(event: DragEndEvent) {
    setDragged(null);
    const { active, over } = event;
    if (!over) return;

    const activeTask = tasks.find((task) => task.id === active.id);
    if (!activeTask) return;

    // Бросок на карточку и на пустую колонку приходят по-разному.
    const overTask = tasks.find((task) => task.id === over.id);
    const targetColumnId: number | null = overTask
      ? overTask.column_id
      : ((over.data.current as { columnId?: number | null } | undefined)?.columnId ?? null);

    const siblings = (grouped.get(columnKey(targetColumnId)) ?? []).filter(
      (task) => task.id !== activeTask.id,
    );

    let afterId: number | null;
    let beforeId: number | null = null;
    if (overTask && overTask.id !== activeTask.id) {
      const index = siblings.findIndex((task) => task.id === overTask.id);
      const movingDown =
        activeTask.column_id === targetColumnId && activeTask.position < overTask.position;
      if (movingDown) {
        afterId = overTask.id;
        beforeId = siblings[index + 1]?.id ?? null;
      } else {
        beforeId = overTask.id;
        afterId = siblings[index - 1]?.id ?? null;
      }
    } else {
      // Пустая колонка или конец списка.
      afterId = siblings[siblings.length - 1]?.id ?? null;
    }

    if (
      activeTask.column_id === targetColumnId &&
      afterId === null &&
      beforeId === null &&
      siblings.length === 0
    ) {
      return;
    }

    move.mutate({
      id: activeTask.id,
      column_id: targetColumnId,
      after_id: afterId,
      before_id: beforeId,
    });
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setDragged(null)}
    >
      <div className="flex gap-4 overflow-x-auto pb-2">
        {orphans.length > 0 && (
          <Column
            column={{ id: null, title: 'Без колонки' }}
            tasks={orphans}
            onOpenTask={onOpenTask}
            onDeleteTask={(task) => remove.mutate(task.id)}
          />
        )}
        {columns.map((column) => (
          <Column
            key={column.id}
            column={column}
            tasks={grouped.get(String(column.id)) ?? []}
            onOpenTask={onOpenTask}
            onDeleteTask={(task) => remove.mutate(task.id)}
          />
        ))}
      </div>

      {/*
        Оверлей рисует карточку под курсором, пока идёт перетаскивание.
        dropAnimation={null}: анимация возврата оставляла копию карточки в DOM
        уже после того, как список обновился, и на экране было две версии одной
        задачи — старая без отметки о выполнении.
      */}
      <DragOverlay dropAnimation={null}>
        {dragged && (
          <div className="w-72 rotate-1">
            <TaskCard task={dragged} onOpen={() => {}} onDelete={() => {}} />
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
};
