import type { components, paths } from './schema';

/**
 * Типы приходят из OpenAPI бэкенда (`npm run gen:api`), а не пишутся руками:
 * расхождение контракта ломает сборку, а не рантайм пользователя.
 */
export type MetaField = components['schemas']['MetaFieldResponse'];
export type Task = components['schemas']['TaskResponse'];
export type Project = components['schemas']['ProjectResponse'];
export type CurrentUser = components['schemas']['CurrentUserResponse'];
export type BoardColumn = components['schemas']['BoardColumnResponse'];
export type Tag = components['schemas']['TagResponse'];
export type Category = components['schemas']['CategoryResponse'];
export type Attachment = components['schemas']['AttachmentResponse'];
export type NotificationPrefs = components['schemas']['NotificationPrefsSchema'];
/** Что клиент вправе менять сам: чат подключается только подтверждением кода. */
export type NotificationPrefsUpdate = components['schemas']['NotificationPrefsUpdate'];
export type Analytics = components['schemas']['AnalyticsResponse'];
export type AssistAction = components['schemas']['AssistRequest']['action'];
export type AssistResult = components['schemas']['AssistResponse'];
export type OtpPurpose = components['schemas']['OtpVerifyRequest']['purpose'];
export type OtpChallenge = components['schemas']['OtpChallengeResponse'];
export type OAuthProvider = components['schemas']['OAuthProvidersResponse']['providers'][number];

/**
 * Тела исходящих запросов берутся из той же схемы, что и ответы. Свободный
 * Record<string, unknown> принимал бы опечатку в имени поля: сервер молча
 * проигнорировал бы её или ответил 422 уже у пользователя.
 */
export type TaskCreateInput = components['schemas']['TaskCreate'];
export type TaskUpdateInput = components['schemas']['TaskUpdate'];
export type TaskMoveInput = components['schemas']['TaskMove'];
export type BoardColumnUpdateInput = components['schemas']['BoardColumnUpdate'];

/** Параметры списка задач: имена и типы фильтров тоже задаёт контракт. */
export type TaskListQuery = NonNullable<paths['/api/v1/tasks']['get']['parameters']['query']>;

/** Документ Tiptap: структура ProseMirror, а не HTML. */
export type RichDocument = Record<string, unknown>;

/** Значение динамического атрибута: date приходит строкой YYYY-MM-DD. */
export type AttributeValue = string | boolean;
