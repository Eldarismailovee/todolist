import { useEffect, useId, useRef, useState } from 'react';

import {
  useAttributeMeta,
  useCategories,
  useCreateTag,
  useTags,
  useUpdateTask,
} from '../api/queries';
import type { AttributeValue, RichDocument, Task } from '../api/types';
import type { AttributeDraft } from '../lib/fields';
import { attributesDraft, attributesPayload, fieldRoot, invalidProps } from '../lib/fields';
import { describeError, fieldErrors } from '../lib/http';
import { AiAssistant } from './AiAssistant';
import { AttributeFields } from './AttributeFields';
import { FieldError } from './FieldError';
import { RichTextEditor } from './RichTextEditor';

interface Props {
  task: Task;
  userId: number;
  onClose: () => void;
}

const field =
  'w-full rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800';

/**
 * Поля формы, у которых есть собственное место для ошибки. Порядок совпадает
 * с визуальным: после отказа фокус уходит к первому неверному полю.
 */
const FOCUSABLE_FIELDS = ['title', 'due_at', 'category_id', 'description'] as const;
const SHOWN_FIELDS = new Set<string>([...FOCUSABLE_FIELDS, 'content', 'tag_ids', 'attributes']);

/** `datetime-local` понимает только «YYYY-MM-DDTHH:mm» в местной зоне. */
function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export const TaskDialog = ({ task, userId, onClose }: Props) => {
  const titleId = useId();
  const descriptionId = useId();
  const dueId = useId();
  const categoryId = useId();

  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? '');
  const [content, setContent] = useState<RichDocument | null>(
    (task.content as RichDocument | null) ?? null,
  );
  const [dueAt, setDueAt] = useState(toLocalInput(task.due_at));
  const [category, setCategory] = useState<number | null>(task.category_id);
  const [tagIds, setTagIds] = useState<number[]>((task.tags ?? []).map((tag) => tag.id));
  const [newTag, setNewTag] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const inputs = useRef<Record<string, HTMLElement | null>>({});

  const tags = useTags(userId);
  const categories = useCategories(userId);
  const attributeMeta = useAttributeMeta();
  const createTag = useCreateTag(userId);
  const update = useUpdateTask(userId);

  const metaFields = attributeMeta.data ?? [];
  // Черновик появляется после первой правки: до этого показываются сохранённые
  // значения. Эффект синхронизации затирал бы ввод при фоновом обновлении.
  const [attributeDraft, setAttributeDraft] = useState<AttributeDraft | null>(null);
  const attributeValues = attributeDraft ?? attributesDraft(metaFields, task.attributes);

  function setAttribute(code: string, value: AttributeValue | null) {
    setAttributeDraft({ ...attributeValues, [code]: value });
  }

  // Esc закрывает диалог: без клавиатуры модальное окно недоступно.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function save() {
    setErrors({});
    try {
      await update.mutateAsync({
        id: task.id,
        patch: {
          title: title.trim() || task.title,
          description: description.trim() || null,
          content,
          // Локальное время приводим к ISO с зоной, иначе сервер получит смещение.
          due_at: dueAt ? new Date(dueAt).toISOString() : null,
          category_id: category,
          tag_ids: tagIds,
          ...(metaFields.length > 0
            ? { attributes: attributesPayload(metaFields, attributeValues) }
            : {}),
        },
      });
      onClose();
    } catch (error) {
      // Отказ mutateAsync обязателен к обработке: без catch он становится
      // необработанным rejection, а диалог закрывался бы как при успехе.
      const fields = fieldErrors(error);
      setErrors(fields);
      const first = FOCUSABLE_FIELDS.find((name) => fields[name]);
      if (first) inputs.current[first]?.focus();
    }
  }

  // Ошибка по самому списку тегов и по конкретному элементу (tag_ids.0)
  // показывается рядом со списком; всё остальное — общим сообщением, иначе
  // серверный отказ по неизвестному форме полю исчез бы с экрана.
  const tagsError = Object.entries(errors).find(([path]) => fieldRoot(path) === 'tag_ids')?.[1];
  // Ошибки динамических полей приходят как attributes.<код>.
  const knownCodes = new Set(metaFields.map((meta) => meta.code));
  const attributeErrors = Object.fromEntries(
    Object.entries(errors)
      .filter(([path]) => path.startsWith('attributes.'))
      .map(([path, message]) => [path.slice('attributes.'.length), message] as const)
      .filter(([code]) => knownCodes.has(code)),
  );
  // Атрибут, которого нет в справочнике этого клиента, показать у поля негде:
  // такая ошибка должна попасть в общее сообщение, а не исчезнуть.
  const isShown = (path: string): boolean =>
    path.startsWith('attributes.')
      ? knownCodes.has(path.slice('attributes.'.length))
      : SHOWN_FIELDS.has(fieldRoot(path));
  const otherErrors = Object.entries(errors).filter(([path]) => !isShown(path));
  const hasShownErrors = Object.keys(errors).some(isShown);

  function toggleTag(id: number) {
    setTagIds((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Редактирование задачи"
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="my-8 w-full max-w-3xl space-y-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-2xl dark:border-gray-800 dark:bg-gray-950">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1 space-y-1.5">
            <label
              htmlFor={titleId}
              className="text-xs font-medium text-gray-500 dark:text-gray-400"
            >
              Заголовок
            </label>
            <input
              id={titleId}
              ref={(element) => {
                inputs.current.title = element;
              }}
              value={title}
              maxLength={255}
              onChange={(event) => setTitle(event.target.value)}
              className={`${field} text-base font-semibold`}
              {...invalidProps(titleId, errors.title)}
            />
            <FieldError id={`${titleId}-error`} message={errors.title} />
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-xl px-2 py-1 text-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            ×
          </button>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor={dueId} className="text-xs font-medium text-gray-500 dark:text-gray-400">
              Срок выполнения
            </label>
            <input
              id={dueId}
              ref={(element) => {
                inputs.current.due_at = element;
              }}
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
              className={field}
              {...invalidProps(dueId, errors.due_at)}
            />
            <FieldError id={`${dueId}-error`} message={errors.due_at} />
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor={categoryId}
              className="text-xs font-medium text-gray-500 dark:text-gray-400"
            >
              Категория
            </label>
            <select
              id={categoryId}
              ref={(element) => {
                inputs.current.category_id = element;
              }}
              value={category ?? ''}
              onChange={(event) =>
                setCategory(event.target.value ? Number(event.target.value) : null)
              }
              className={field}
              {...invalidProps(categoryId, errors.category_id)}
            >
              <option value="">Без категории</option>
              {categories.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <FieldError id={`${categoryId}-error`} message={errors.category_id} />
          </div>
        </div>

        <div className="space-y-1.5">
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400">Теги</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {tags.data?.map((tag) => (
              <button
                key={tag.id}
                type="button"
                aria-pressed={tagIds.includes(tag.id)}
                onClick={() => toggleTag(tag.id)}
                className={[
                  'rounded-lg border px-2 py-1 text-xs transition-all',
                  tagIds.includes(tag.id)
                    ? 'border-indigo-500 bg-indigo-600/10 text-indigo-700 dark:text-indigo-300'
                    : 'border-gray-200 hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800',
                ].join(' ')}
              >
                #{tag.name}
              </button>
            ))}
            <input
              value={newTag}
              placeholder="новый тег"
              maxLength={40}
              onChange={(event) => setNewTag(event.target.value)}
              onKeyDown={async (event) => {
                if (event.key !== 'Enter' || !newTag.trim()) return;
                event.preventDefault();
                const created = await createTag.mutateAsync(newTag.trim());
                setTagIds((current) => [...current, created.id]);
                setNewTag('');
              }}
              className="w-28 rounded-lg border border-dashed border-gray-300 bg-transparent px-2 py-1 text-xs outline-none focus:border-indigo-500 dark:border-gray-700"
            />
          </div>
          <FieldError id="task-tags-error" message={tagsError} />
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor={descriptionId}
            className="text-xs font-medium text-gray-500 dark:text-gray-400"
          >
            Краткое описание
          </label>
          <input
            id={descriptionId}
            ref={(element) => {
              inputs.current.description = element;
            }}
            value={description}
            maxLength={10_000}
            onChange={(event) => setDescription(event.target.value)}
            className={field}
            {...invalidProps(descriptionId, errors.description)}
          />
          <FieldError id={`${descriptionId}-error`} message={errors.description} />
        </div>

        {metaFields.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
              Дополнительные поля
            </span>
            <AttributeFields
              fields={metaFields}
              values={attributeValues}
              errors={attributeErrors}
              onChange={setAttribute}
              idPrefix={`task-${task.id}-attr`}
            />
          </div>
        )}

        <div className="space-y-1.5">
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400">Содержимое</span>
          <RichTextEditor value={content} onChange={setContent} />
          <FieldError id="task-content-error" message={errors.content} />
        </div>

        <AiAssistant
          source={`${title}\n${description}`.trim()}
          applyLabel="В описание"
          onApply={(item) => setDescription((current) => (current ? `${current}; ${item}` : item))}
        />

        {update.isError && (
          <div role="alert" className="space-y-1 text-xs text-red-500">
            {otherErrors.map(([path, message]) => (
              <p key={path}>{`${path}: ${message}`}</p>
            ))}
            {!hasShownErrors && otherErrors.length === 0 && <p>{describeError(update.error)}</p>}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-gray-200 px-4 py-2 text-sm transition-all hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={update.isPending}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
          >
            {update.isPending ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      </div>
    </div>
  );
};
