import type { AttributeValue, MetaField } from '../api/types';
import type { AttributeDraft } from '../lib/fields';
import { invalidProps, orderMetaFields } from '../lib/fields';
import { FieldError } from './FieldError';

interface Props {
  /** Справочник с сервера: набор полей задаёт администратор, а не клиент. */
  fields: MetaField[];
  values: AttributeDraft;
  /** Ошибки по коду атрибута (из `attributes.<code>` в ответе 422). */
  errors?: Record<string, string>;
  onChange: (code: string, value: AttributeValue | null) => void;
  idPrefix: string;
}

const control =
  'w-full rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800';

/**
 * Форма динамических атрибутов строится по серверным метаданным. Без неё
 * обязательное поле, добавленное администратором, делает создание задачи
 * невозможным: форма не отправляет значение и не даёт его ввести.
 */
export const AttributeFields = ({ fields, values, errors = {}, onChange, idPrefix }: Props) => {
  if (fields.length === 0) return null;

  return (
    <div className="space-y-3">
      {orderMetaFields(fields).map((field) => {
        const id = `${idPrefix}-${field.code}`;
        const message = errors[field.code];
        const value = values[field.code];

        if (field.type === 'boolean') {
          return (
            <div key={field.code} className="space-y-1.5">
              <div className="flex items-center justify-between gap-3">
                <label htmlFor={id} className="text-sm">
                  {field.title}
                  {field.is_required && <span aria-hidden="true"> *</span>}
                </label>
                <input
                  id={id}
                  type="checkbox"
                  checked={value === true}
                  required={field.is_required}
                  onChange={(event) => onChange(field.code, event.target.checked)}
                  className="h-5 w-5 rounded accent-indigo-600"
                  {...invalidProps(id, message)}
                />
              </div>
              <FieldError id={`${id}-error`} message={message} />
            </div>
          );
        }

        return (
          <div key={field.code} className="space-y-1.5">
            <label htmlFor={id} className="text-xs font-medium text-gray-500 dark:text-gray-400">
              {field.title}
              {field.is_required && <span aria-hidden="true"> *</span>}
            </label>
            <input
              id={id}
              type={field.type === 'date' ? 'date' : 'text'}
              value={typeof value === 'string' ? value : ''}
              required={field.is_required}
              aria-required={field.is_required || undefined}
              onChange={(event) => onChange(field.code, event.target.value)}
              className={control}
              {...invalidProps(id, message)}
            />
            <FieldError id={`${id}-error`} message={message} />
          </div>
        );
      })}
    </div>
  );
};
