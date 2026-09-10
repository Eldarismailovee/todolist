import type { components } from './schema';

/**
 * Типы приходят из OpenAPI бэкенда (`npm run gen:api`), а не пишутся руками:
 * расхождение контракта ломает сборку, а не рантайм пользователя.
 */
export type MetaField = components['schemas']['MetaFieldResponse'];
export type Task = components['schemas']['TaskResponse'];
export type Project = components['schemas']['ProjectResponse'];
export type CurrentUser = components['schemas']['CurrentUserResponse'];
export type TaskCreate = components['schemas']['TaskCreate'];

/** Значение динамического атрибута в форме: date приходит строкой YYYY-MM-DD. */
export type AttributeValue = string | boolean;
