import type { MetaField } from '../api/types';

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
