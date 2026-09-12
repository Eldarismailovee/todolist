import { Suspense, lazy, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import {
  useAttributeMeta,
  useCategories,
  useColumns,
  useCreateColumn,
  useCreateTask,
  useTags,
  useTasks,
  type TaskFilters,
} from '../api/queries';
import type { AttributeValue, Task } from '../api/types';
import type { AttributeDraft } from '../lib/fields';
import { attributesPayload } from '../lib/fields';
import { describeError, fieldErrors } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { AiAssistant } from './AiAssistant';
import { AttributeFields } from './AttributeFields';
import { KanbanBoard } from './KanbanBoard';

/**
 * Диалог тянет за собой Tiptap со всеми расширениями. Пока задачу не открыли,
 * этот код не нужен.
 */
const TaskDialog = lazy(() =>
  import('./TaskDialog').then((module) => ({ default: module.TaskDialog })),
);

const field =
  'rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800';

const Tile = ({ label, value, hint }: { label: string; value: number; hint?: string }) => (
  <div className="bento-card rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
    <p className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</p>
    <p className="mt-1 text-3xl font-bold tracking-tight">{value}</p>
    {hint && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
  </div>
);

export const ProjectBoard = () => {
  const { projectId: projectIdParam } = useParams();
  const user = useAuthStore((state) => state.user);
  const userId = user?.id ?? 0;

  const projectId = Number(projectIdParam);
  const validProject = Number.isInteger(projectId) && projectId > 0;

  const [search, setSearch] = useState('');
  const [tagFilter, setTagFilter] = useState<number[]>([]);
  const [categoryFilter, setCategoryFilter] = useState<number | null>(null);
  const [hideCompleted, setHideCompleted] = useState(false);
  const [openTask, setOpenTask] = useState<Task | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [newColumn, setNewColumn] = useState('');
  const [draftAttributes, setDraftAttributes] = useState<AttributeDraft>({});
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({});

  const filters: TaskFilters = useMemo(
    () => ({
      q: search.trim() || undefined,
      tagIds: tagFilter,
      categoryId: categoryFilter,
      completed: hideCompleted ? false : null,
    }),
    [search, tagFilter, categoryFilter, hideCompleted],
  );

  const columns = useColumns(userId, validProject ? projectId : null);
  const tasks = useTasks(userId, validProject ? projectId : null, filters);
  const tags = useTags(userId);
  const categories = useCategories(userId);
  const attributeMeta = useAttributeMeta();
  const createTask = useCreateTask(userId, projectId);
  const createColumn = useCreateColumn(userId, projectId);

  // В быстрой форме показываются только обязательные поля: без них задачу
  // нельзя создать вовсе. Необязательные заполняются в диалоге задачи.
  const requiredFields = (attributeMeta.data ?? []).filter((meta) => meta.is_required);

  if (!validProject) {
    return <p className="text-sm text-red-500">Некорректный идентификатор проекта.</p>;
  }

  const list = tasks.data ?? [];
  const open = list.filter((task) => task.completed_at === null).length;
  const overdue = list.filter(
    (task) => task.due_at && task.completed_at === null && new Date(task.due_at) < new Date(),
  ).length;

  async function addTask(title: string) {
    const trimmed = title.trim();
    if (!trimmed) return;
    setCreateErrors({});
    try {
      // Новая задача попадает в первую колонку доски.
      await createTask.mutateAsync({
        title: trimmed,
        column_id: columns.data?.[0]?.id ?? null,
        ...(requiredFields.length > 0
          ? { attributes: attributesPayload(requiredFields, draftAttributes) }
          : {}),
      });
      setDraftTitle('');
      setDraftAttributes({});
    } catch (error) {
      // Отказ нужно показать у поля: обязательный атрибут иначе выглядит как
      // необъяснимый сбой создания задачи.
      setCreateErrors(fieldErrors(error));
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Tile label="В работе" value={open} hint={`Всего задач: ${list.length}`} />
        <Tile label="Просрочено" value={overdue} hint="срок прошёл, задача открыта" />
        <Tile label="Колонок на доске" value={columns.data?.length ?? 0} />
      </div>

      {/* Фильтры одной строкой над доской. */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={search}
          placeholder="Поиск по задачам…"
          aria-label="Поиск по задачам"
          onChange={(event) => setSearch(event.target.value)}
          className={`${field} min-w-[12rem] flex-1`}
        />
        <select
          value={categoryFilter ?? ''}
          aria-label="Категория"
          onChange={(event) =>
            setCategoryFilter(event.target.value ? Number(event.target.value) : null)
          }
          className={field}
        >
          <option value="">Все категории</option>
          {categories.data?.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
          <input
            type="checkbox"
            checked={hideCompleted}
            onChange={(event) => setHideCompleted(event.target.checked)}
            className="h-4 w-4 rounded accent-indigo-600"
          />
          Скрыть выполненные
        </label>
      </div>

      {tags.data && tags.data.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tags.data.map((tag) => {
            const active = tagFilter.includes(tag.id);
            return (
              <button
                key={tag.id}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setTagFilter((current) =>
                    active ? current.filter((id) => id !== tag.id) : [...current, tag.id],
                  )
                }
                className={[
                  'rounded-lg border px-2 py-1 text-xs transition-all',
                  active
                    ? 'border-indigo-500 bg-indigo-600/10 text-indigo-700 dark:text-indigo-300'
                    : 'border-gray-200 hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800',
                ].join(' ')}
              >
                #{tag.name}
              </button>
            );
          })}
        </div>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void addTask(draftTitle);
        }}
        className="space-y-3 rounded-2xl border border-gray-200 bg-white/70 p-4 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70"
      >
        <div className="flex flex-wrap gap-2">
          <label htmlFor="new-task" className="sr-only">
            Новая задача
          </label>
          <input
            id="new-task"
            value={draftTitle}
            placeholder="Новая задача…"
            maxLength={255}
            onChange={(event) => setDraftTitle(event.target.value)}
            className={`${field} min-w-[12rem] flex-1`}
          />
          <button
            type="submit"
            disabled={createTask.isPending || draftTitle.trim().length === 0}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
          >
            Добавить
          </button>
        </div>

        <AttributeFields
          fields={requiredFields}
          values={draftAttributes}
          errors={Object.fromEntries(
            Object.entries(createErrors)
              .filter(([path]) => path.startsWith('attributes.'))
              .map(([path, message]) => [path.slice('attributes.'.length), message]),
          )}
          onChange={(code: string, value: AttributeValue | null) =>
            setDraftAttributes((current) => ({ ...current, [code]: value }))
          }
          idPrefix="new-task-attr"
        />
      </form>

      {createTask.isError && (
        <p role="alert" className="text-xs text-red-500">
          {describeError(createTask.error)}
        </p>
      )}

      {tasks.isPending && <p className="text-sm text-gray-500">Загружаем задачи…</p>}
      {tasks.isError && (
        <p role="alert" className="text-sm text-red-500">
          {describeError(tasks.error)}
        </p>
      )}

      {columns.data && (
        <KanbanBoard columns={columns.data} tasks={list} userId={userId} onOpenTask={setOpenTask} />
      )}

      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!newColumn.trim()) return;
          await createColumn.mutateAsync(newColumn.trim());
          setNewColumn('');
        }}
        className="flex flex-wrap gap-2"
      >
        <label htmlFor="new-column" className="sr-only">
          Новая колонка
        </label>
        <input
          id="new-column"
          value={newColumn}
          placeholder="Новая колонка…"
          maxLength={120}
          onChange={(event) => setNewColumn(event.target.value)}
          className={`${field} w-48`}
        />
        <button
          type="submit"
          disabled={createColumn.isPending || newColumn.trim().length === 0}
          className="rounded-xl border border-gray-200 px-3 py-2 text-sm transition-all hover:bg-gray-100 disabled:opacity-60 dark:border-gray-800 dark:hover:bg-gray-800"
        >
          + колонка
        </button>
      </form>

      <AiAssistant
        source={draftTitle}
        applyLabel="Создать задачу"
        onApply={(item) => void addTask(item)}
      />

      {openTask && (
        <Suspense fallback={null}>
          <TaskDialog
            // Диалог берёт свежую версию задачи из списка после инвалидации.
            task={list.find((task) => task.id === openTask.id) ?? openTask}
            userId={userId}
            onClose={() => setOpenTask(null)}
          />
        </Suspense>
      )}
    </div>
  );
};
