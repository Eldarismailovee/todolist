import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import React, { useEffect, useMemo } from 'react';
import { Controller, useForm } from 'react-hook-form';
import * as z from 'zod';

import { queryKeys } from '../api/queries';
import type { MetaField, Task } from '../api/types';
import { orderMetaFields } from '../lib/fields';
import { api, describeError, fieldErrors } from '../lib/http';

interface Props {
  metaFields: MetaField[];
  projectId: number;
  userId: number;
}

type FormValues = Record<string, string | boolean | undefined>;

/** Существующая календарная дата: 2026-02-30 отсекается до отправки. */
const isoDate = z
  .string()
  .regex(/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/, 'Укажите дату в формате ГГГГ-ММ-ДД')
  .refine((value) => {
    const parsed = new Date(`${value}T00:00:00Z`);
    return (
      value.slice(0, 4) !== '0000' &&
      !Number.isNaN(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    );
  }, 'Такой даты не существует');

function defaultsFor(metaFields: MetaField[]): FormValues {
  return metaFields.reduce<FormValues>(
    (accumulator, field) => ({
      ...accumulator,
      [field.code]: field.type === 'boolean' ? false : '',
    }),
    {},
  );
}

export const DynamicTaskForm: React.FC<Props> = ({ metaFields: rawFields, projectId, userId }) => {
  const queryClient = useQueryClient();
  // Порядок для чтения, а не порядок хранения справочника.
  const metaFields = useMemo(() => orderMetaFields(rawFields), [rawFields]);

  // Мемоизация: без неё схема пересобиралась бы на каждый ввод символа.
  const dynamicSchema = useMemo(() => {
    const shape: Record<string, z.ZodTypeAny> = {};
    for (const field of metaFields) {
      if (field.type === 'boolean') {
        shape[field.code] = z.boolean();
      } else if (field.type === 'date') {
        shape[field.code] = field.is_required
          ? isoDate
          : z.preprocess((value) => (value === '' ? undefined : value), isoDate.optional());
      } else {
        const text = z.string().trim().max(2000, 'Не длиннее 2000 символов');
        shape[field.code] = field.is_required
          ? text.min(1, `${field.title}: обязательное поле`)
          : text.optional();
      }
    }
    return z.object(shape);
  }, [metaFields]);

  const {
    handleSubmit,
    control,
    reset,
    setError,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(dynamicSchema),
    defaultValues: defaultsFor(metaFields),
  });

  // Подпись содержимого, а не ссылка на массив: сброс должен происходить при
  // реальном изменении справочника, а не на каждый рендер родителя.
  const signature = useMemo(
    () => metaFields.map((field) => `${field.code}:${field.type}:${field.is_required}`).join('|'),
    [metaFields],
  );

  // Справочник может обновиться асинхронно: схема и значения по умолчанию
  // должны меняться согласованно, иначе в форме останутся снятые поля.
  useEffect(() => {
    reset(defaultsFor(metaFields));
    // metaFields намеренно не в зависимостях: его заменяет signature.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, reset]);

  const createTask = useMutation({
    mutationFn: async (attributes: Record<string, unknown>): Promise<Task> =>
      (await api.post('/tasks', { project_id: projectId, attributes })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks(userId, projectId) });
      reset(defaultsFor(metaFields));
    },
    onError: (error) => {
      // Полевые ошибки 422 показываем у конкретных полей, а не только сверху.
      for (const [field, message] of Object.entries(fieldErrors(error))) {
        setError(field, { type: 'server', message });
      }
    },
  });

  const submit = handleSubmit((values) => {
    // Пустые необязательные строки не отправляем: сервер нормализует
    // отсутствие ключа, а пустая строка — это значение.
    const attributes: Record<string, unknown> = {};
    for (const field of metaFields) {
      const value = values[field.code];
      if (value === undefined || value === '') continue;
      attributes[field.code] = value;
    }
    createTask.mutate(attributes);
  });

  if (metaFields.length === 0) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        Администратор ещё не настроил поля задач.
      </p>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-4 rounded-2xl border border-gray-200 bg-white/70 p-6 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70"
    >
      <h3 className="text-lg font-bold tracking-tight">Новая задача</h3>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {metaFields.map((field) => {
          const inputId = `task-field-${field.code}`;
          const message = errors[field.code]?.message;
          return (
            <div key={field.code} className="flex flex-col gap-1.5">
              <label
                htmlFor={inputId}
                className="text-xs font-medium text-gray-500 dark:text-gray-400"
              >
                {field.title}{' '}
                {field.is_required && (
                  <span className="text-red-500" aria-hidden="true">
                    *
                  </span>
                )}
              </label>
              <Controller
                name={field.code}
                control={control}
                render={({ field: renderProps }) =>
                  field.type === 'boolean' ? (
                    <input
                      id={inputId}
                      type="checkbox"
                      checked={Boolean(renderProps.value)}
                      onChange={(event) => renderProps.onChange(event.target.checked)}
                      onBlur={renderProps.onBlur}
                      ref={renderProps.ref}
                      aria-invalid={message ? true : undefined}
                      className="h-5 w-5 rounded accent-indigo-600"
                    />
                  ) : (
                    <input
                      id={inputId}
                      type={field.type === 'date' ? 'date' : 'text'}
                      // Без атрибута required: иначе встроенная проверка
                      // браузера блокирует submit и сообщения zod не
                      // показываются. Валидацию ведёт одна система.
                      aria-required={field.is_required}
                      value={typeof renderProps.value === 'string' ? renderProps.value : ''}
                      onChange={renderProps.onChange}
                      onBlur={renderProps.onBlur}
                      ref={renderProps.ref}
                      aria-invalid={message ? true : undefined}
                      className="rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800"
                    />
                  )
                }
              />
              {message && <span className="text-xs text-red-500">{String(message)}</span>}
            </div>
          );
        })}
      </div>

      {createTask.isError && (
        <p role="alert" className="text-xs text-red-500">
          {describeError(createTask.error)}
        </p>
      )}

      <button
        type="submit"
        disabled={createTask.isPending}
        className="w-full rounded-xl bg-indigo-600 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
      >
        {createTask.isPending ? 'Создаём…' : 'Создать'}
      </button>
    </form>
  );
};
