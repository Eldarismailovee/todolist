import React, { useMemo } from 'react';
import { useParams } from 'react-router-dom';

import {
  useAttributeMeta,
  useDeleteTask,
  useTasks,
  useToggleTaskAttribute,
} from '../api/queries';
import type { MetaField, Task } from '../api/types';
import { orderMetaFields, primaryField } from '../lib/fields';
import { describeError } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { DynamicTaskForm } from './DynamicTaskForm';

function formatValue(field: MetaField, value: unknown): string {
  if (field.type === 'boolean') return value ? 'да' : 'нет';
  if (value === undefined || value === '') return '—';
  return String(value);
}

function titleOf(task: Task, metaFields: MetaField[]): string {
  const field = primaryField(metaFields);
  const value = field ? task.attributes[field.code] : undefined;
  return typeof value === 'string' && value.length > 0 ? value : `Задача #${task.id}`;
}

const TaskCard: React.FC<{
  task: Task;
  metaFields: MetaField[];
  userId: number;
  projectId: number;
  emphasis: boolean;
}> = ({ task, metaFields, userId, projectId, emphasis }) => {
  const toggle = useToggleTaskAttribute(userId, projectId);
  const remove = useDeleteTask(userId, projectId);
  const booleanField = metaFields.find((field) => field.type === 'boolean');
  // Заголовок уже показан крупно — в списке полей он дублировался бы.
  const primary = primaryField(metaFields);
  const detailFields = metaFields.filter((field) => field.code !== primary?.code);
  const done = booleanField ? Boolean(task.attributes[booleanField.code]) : false;

  return (
    <article
      className={[
        'bento-card flex flex-col justify-between gap-3 rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70',
        emphasis ? 'sm:col-span-2' : '',
        done ? 'opacity-70' : '',
      ].join(' ')}
    >
      <div className="space-y-2">
        <h4
          className={[
            'text-base font-semibold tracking-tight',
            done ? 'line-through decoration-gray-400' : '',
          ].join(' ')}
        >
          {titleOf(task, metaFields)}
        </h4>
        <dl className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
          {detailFields.map((field) => (
            <div key={field.code} className="flex gap-2">
              <dt className="shrink-0">{field.title}:</dt>
              <dd className="truncate text-gray-700 dark:text-gray-300">
                {formatValue(field, task.attributes[field.code])}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="flex items-center gap-2">
        {booleanField && (
          <button
            type="button"
            disabled={toggle.isPending}
            onClick={() =>
              toggle.mutate({
                taskId: task.id,
                // PATCH заменяет набор целиком, поэтому отправляем все атрибуты.
                attributes: { ...task.attributes, [booleanField.code]: !done },
              })
            }
            className="rounded-xl border border-gray-200 px-3 py-1.5 text-xs font-medium transition-all hover:bg-gray-100 disabled:opacity-60 dark:border-gray-800 dark:hover:bg-gray-800"
          >
            {done ? `Снять «${booleanField.title}»` : booleanField.title}
          </button>
        )}
        <button
          type="button"
          disabled={remove.isPending}
          onClick={() => remove.mutate(task.id)}
          className="rounded-xl px-3 py-1.5 text-xs font-medium text-red-600 transition-all hover:bg-red-50 disabled:opacity-60 dark:text-red-400 dark:hover:bg-red-950/40"
        >
          Удалить
        </button>
      </div>
    </article>
  );
};

export const BentoTaskDashboard: React.FC = () => {
  const { projectId: projectIdParam } = useParams();
  const user = useAuthStore((state) => state.user);
  const userId = user?.id ?? 0;

  const projectId = Number(projectIdParam);
  const validProject = Number.isInteger(projectId) && projectId > 0;

  const meta = useAttributeMeta();
  const tasks = useTasks(userId, validProject ? projectId : null);

  // Мемоизация обязательна: новый массив на каждый рендер заставлял бы форму
  // пересобирать схему и сбрасывать введённые значения.
  const metaFields = useMemo(() => orderMetaFields(meta.data ?? []), [meta.data]);

  if (!validProject) {
    return <p className="text-sm text-red-500">Некорректный идентификатор проекта.</p>;
  }

  const done = metaFields.find((field) => field.type === 'boolean');
  const openCount = tasks.data?.filter((task) => !done || !task.attributes[done.code]).length ?? 0;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <div className="bento-card rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md sm:col-span-2 dark:border-gray-800 dark:bg-gray-900/70">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400">В работе</p>
          <p className="mt-1 text-3xl font-bold tracking-tight">{openCount}</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Всего задач: {tasks.data?.length ?? 0}
          </p>
        </div>
        <div className="bento-card rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400">Полей в карточке</p>
          <p className="mt-1 text-3xl font-bold tracking-tight">{metaFields.length}</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Справочник задаёт администратор
          </p>
        </div>
      </div>

      {meta.isError && (
        <p role="alert" className="text-sm text-red-500">
          {describeError(meta.error)}
        </p>
      )}

      {meta.isSuccess && (
        <DynamicTaskForm metaFields={metaFields} projectId={projectId} userId={userId} />
      )}

      {tasks.isPending && <p className="text-sm text-gray-500">Загружаем задачи…</p>}
      {tasks.isError && (
        <p role="alert" className="text-sm text-red-500">
          {describeError(tasks.error)}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {tasks.data?.map((task, index) => (
          <TaskCard
            key={task.id}
            task={task}
            metaFields={metaFields}
            userId={userId}
            projectId={projectId}
            // Асимметрия Bento-сетки: каждая пятая карточка шире.
            emphasis={index % 5 === 0}
          />
        ))}
      </div>

      {tasks.isSuccess && tasks.data.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          В этом проекте пока нет задач.
        </p>
      )}
    </div>
  );
};
