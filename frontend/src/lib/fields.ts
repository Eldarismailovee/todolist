import type { AttributeValue, MetaField } from '../api/types';

/** Значения формы динамических атрибутов: пустая строка — «не заполнено». */
export type AttributeDraft = Record<string, AttributeValue | null>;

/**
 * Справочник приходит отсортированным по коду — это порядок хранения, а не
 * порядок для чтения: обязательное «Название» оказалось бы после
 * необязательной «Заметки». Раскладку задаём один раз и используем и в форме,
 * и в карточке, чтобы они не расходились.
 */

/** Поле-заголовок: обязательная строка, иначе первая строка справочника. */
export function primaryField(metaFields: MetaField[]): MetaField | undefined {
  return (
    metaFields.find((field) => field.type === 'string' && field.is_required) ??
    metaFields.find((field) => field.type === 'string')
  );
}

/** Заголовок, затем остальные обязательные, затем необязательные. */
export function orderMetaFields(metaFields: MetaField[]): MetaField[] {
  const primary = primaryField(metaFields);
  const rank = (field: MetaField): number => {
    if (primary && field.code === primary.code) return 0;
    return field.is_required ? 1 : 2;
  };
  return [...metaFields].sort((left, right) => rank(left) - rank(right));
}

/**
 * Значения для отправки. Пустая строка и пустая дата — это «не заполнено»,
 * то есть null: сервер нормализует его в отсутствие ключа. Подставлять вместо
 * незаполненного обязательного поля выдуманное значение нельзя — данные
 * принадлежат пользователю, и сервер обязан ответить 422.
 */
export function attributesPayload(fields: MetaField[], values: AttributeDraft): AttributeDraft {
  const payload: AttributeDraft = {};
  for (const field of fields) {
    const value = values[field.code];
    if (field.type === 'boolean') {
      payload[field.code] = value === true;
      continue;
    }
    payload[field.code] = typeof value === 'string' && value.trim() ? value.trim() : null;
  }
  return payload;
}

/** Начальные значения формы из сохранённых атрибутов задачи. */
export function attributesDraft(
  fields: MetaField[],
  stored: Record<string, AttributeValue>,
): AttributeDraft {
  const draft: AttributeDraft = {};
  for (const field of fields) {
    draft[field.code] = stored[field.code] ?? (field.type === 'boolean' ? false : '');
  }
  return draft;
}

/** Верхний уровень пути ошибки: `attributes.deadline` → `attributes`. */
export function fieldRoot(path: string): string {
  return path.split('.')[0] ?? path;
}

/**
 * Атрибуты доступности для поля с ошибкой. Одного цвета рамки недостаточно:
 * программа чтения с экрана узнаёт о неверном значении только из aria-invalid,
 * а текст ошибки получает по aria-describedby.
 */
export function invalidProps(id: string, message?: string) {
  return {
    'aria-invalid': message ? true : undefined,
    'aria-describedby': message ? `${id}-error` : undefined,
  };
}
